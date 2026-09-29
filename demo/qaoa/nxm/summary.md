# QAOA N×M matrix — Hellinger distance to the exact distribution

H_NXM = `-0.8 Z0 Z1 + 0.5 Z1 Z2 - 0.3 Z0 + 0.2`, Layers 2, Betas [-0.67, -0.42], Gammas [1.14, 1.27]. Ground state `001`.

| builder \ engine | QiskitAerSimulator | CirqSimulator | QrispSimulator | PennylaneSimulator | BraketSimulator | QSharpSimulator | PyquilSimulator |
|---|---|---|---|---|---|---|---|
| QiskitQAOACircuit | 0.0103 | 0.0177 | 0.0177 | 0.0120 | 0.0129 | 0.0187 | 0.0439 |
| CirqQAOACircuit | 0.0103 | 0.0159 | 0.0177 | 0.0120 | 0.0206 | 0.0187 | 0.0439 |
| PennylaneQAOACircuit | 0.0103 | 0.0177 | 0.0177 | 0.0120 | 0.0168 | 0.0187 | 0.0439 |
| QrispQAOACircuit | 0.0103 | 0.0159 | 0.0177 | 0.0120 | 0.0163 | 0.0187 | 0.0439 |
| PyquilQAOACircuit | 0.0103 | 0.0159 | 0.0177 | 0.0120 | 0.0147 | 0.0187 | 0.0439 |

Notes: BraketSimulator is unseeded (statistical agreement only, never compared cell-for-cell against a saved value). PyquilSimulator runs at 256 shots (~13.6 ms/shot); every other engine runs at 4096 shots.
