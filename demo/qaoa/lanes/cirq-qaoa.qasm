// Generated from Cirq v1.7.0

OPENQASM 2.0;
include "qelib1.inc";


// Qubits: [q(0), q(1)]
qreg q[2];


h q[0];
h q[1];
cx q[1],q[0];
rz(pi*0.5274711082) q[0];
cx q[1],q[0];
rx(pi*-1.5036595648) q[0];
rx(pi*-1.5036595648) q[1];
cx q[1],q[0];
rz(pi*-0.9723313427) q[0];
cx q[1],q[0];
rx(pi*0.7463855741) q[0];
rx(pi*0.7463855741) q[1];
