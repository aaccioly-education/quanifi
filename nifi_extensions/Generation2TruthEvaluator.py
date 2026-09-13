"""Join frozen Generation-2 decisions to a separately stored truth table."""
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from generation2.core import artifact_digest, metrics  # noqa: E402

from nifiapi.flowfiletransform import FlowFileTransform, FlowFileTransformResult
from nifiapi.properties import PropertyDescriptor, StandardValidators
from nifiapi.relationship import Relationship
from nifiapi.__jvm__ import JvmHolder


class Generation2TruthEvaluator(FlowFileTransform):
    class Java:
        implements = ["org.apache.nifi.python.processor.FlowFileTransform"]

    class ProcessorDetails:
        version = "0.1.0"
        description = ("Evaluates already-frozen Generation-2 decisions against "
                       "truth supplied only after the oracle stage.")
        tags = ["quantum", "generation2", "evaluation", "unblinding"]
        dependencies = []

    def __init__(self, **kwargs):
        JvmHolder.jvm = kwargs.get("jvm")
        super().__init__()
        self.truth_file = PropertyDescriptor(
            name="Sealed Truth File", description="Reviewed Generation-2 truth JSON.",
            required=True, validators=[StandardValidators.NON_EMPTY_VALIDATOR])
        self.truth_file_sha = PropertyDescriptor(
            name="Expected Truth File SHA-256",
            description=("Exact reviewed truth-file digest. Required by sealed "
                         "campaign canvases; legacy canvases may leave it empty."),
            required=False, default_value="")
        self.descriptors = [self.truth_file, self.truth_file_sha]

    def getPropertyDescriptors(self):
        return self.descriptors

    def getRelationships(self):
        return [Relationship(name="success", description="Evaluated decisions."),
                Relationship(name="failure", description="Missing or mismatched truth.")]

    def transform(self, context, flowFile):
        raw = bytes(flowFile.getContentsAsBytes())
        try:
            decision_doc = json.loads(raw.decode("utf-8"))
            if artifact_digest(decision_doc, "decisions_sha256") != decision_doc.get("decisions_sha256"):
                raise ValueError("decision artifact changed after freezing")
            decisions = decision_doc["decisions"]
            path = context.getProperty(self.truth_file).getValue()
            truth_raw = open(path, "rb").read()
            expected_file_sha = (context.getProperty(
                self.truth_file_sha).getValue() or "").strip().lower()
            actual_file_sha = hashlib.sha256(truth_raw).hexdigest()
            if expected_file_sha and actual_file_sha != expected_file_sha:
                raise ValueError("sealed truth file SHA-256 mismatch")
            truth_doc = json.loads(truth_raw.decode("utf-8"))
            if (truth_doc.get("schema") !=
                    "quanifi-generation2-sealed-truth-v1"):
                raise ValueError("unsupported sealed truth schema")
            if (artifact_digest(truth_doc, "sealed_truth_sha256") !=
                    truth_doc.get("sealed_truth_sha256")):
                raise ValueError("sealed truth changed after freezing")
            records = truth_doc.get("records")
            if not isinstance(records, list) or not records:
                raise ValueError("sealed truth has no records")
            truth = {(r.get("job_key"), r["case_id"], r["mutation_id"]): r
                     for r in records}
            if len(truth) != len(records):
                raise ValueError("sealed truth contains duplicate identities")
            if truth_doc.get("campaign_config_sha256"):
                expected_identity = {
                    "campaign_id": truth_doc.get("campaign_id"),
                    "campaign_design_id": truth_doc.get("campaign_design_id"),
                    "campaign_config_sha256": truth_doc.get(
                        "campaign_config_sha256"),
                }
                for decision in decisions:
                    if any(decision.get(key) != value
                           for key, value in expected_identity.items()):
                        raise ValueError(
                            "decision and sealed truth campaign identities differ")
            evaluated = []
            for d in decisions:
                key = (d.get("job_key"), d["case_id"], d["mutation_id"])
                # Canvas decisions may omit job_key when one job is evaluated
                # at a time; require a unique case/mutation fallback.
                row = truth.get(key)
                if row is None:
                    if truth_doc.get("campaign_config_sha256"):
                        raise ValueError("sealed v6 truth has no exact row for %s" %
                                         (key,))
                    matches = [r for r in records
                               if r["case_id"] == d["case_id"] and
                               r["mutation_id"] == d["mutation_id"]]
                    if len(matches) != 1:
                        raise ValueError("truth join is not unique for %s" % (key,))
                    row = matches[0]
                faulty = row["true_faulty_version"]
                if d["decision"] == "abstain": outcome = "abstention"
                elif faulty is None: outcome = "correct_clean" if d["decision"] == "clean" else "clean_false_alarm"
                elif d["decision"] == "clean": outcome = "missed_mutant"
                elif d["suspect_version"] == faulty: outcome = "correctly_localized"
                else: outcome = "wrong_attribution"
                evaluated.append({**d, "outcome": outcome,
                                  "true_faulty_version": faulty,
                                  "mutation_activation": row.get("mutation_activation"),
                                  "operator": row.get("operator"),
                                  "logical_locus": row.get("logical_locus")})
        except Exception as exc:  # noqa: BLE001
            return FlowFileTransformResult(relationship="failure", contents=raw,
                                           attributes={"generation2.error": str(exc)})
        out = {"campaign_generation": 2, "records": evaluated,
               "metrics": metrics(evaluated)}
        return FlowFileTransformResult(
            relationship="success", contents=json.dumps(out, indent=2).encode(),
            attributes={"generation2.evaluated": str(len(evaluated)),
                        "mime.type": "application/json"})
