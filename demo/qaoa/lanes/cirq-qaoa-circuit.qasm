// Generated from Cirq v1.7.0

OPENQASM 2.0;
include "qelib1.inc";


// Qubits: [q(0), q(1), q(2)]
qreg q[3];


h q[0];
h q[1];
h q[2];
cx q[1],q[0];
rz(pi*-0.5805972324) q[0];
cx q[1],q[0];
cx q[2],q[1];
rz(pi*-0.2177239621) q[0];
rz(pi*0.3628732702) q[1];
rx(pi*-0.4265352475) q[0];
cx q[2],q[1];
rx(pi*-0.4265352475) q[1];
rx(pi*-0.4265352475) q[2];
cx q[1],q[0];
rz(pi*-0.6468056887) q[0];
cx q[1],q[0];
cx q[2],q[1];
rz(pi*-0.2425521333) q[0];
rz(pi*0.4042535555) q[1];
rx(pi*-0.2673803044) q[0];
cx q[2],q[1];
rx(pi*-0.2673803044) q[1];
rx(pi*-0.2673803044) q[2];
