pragma circom 2.0.0;

include "node_modules/circomlib/circuits/poseidon.circom";
include "node_modules/circomlib/circuits/comparators.circom";

/**
 * TournamentPassport — Phase 56
 *
 * Proves N=5 consecutive NOMINAL PITL sessions satisfy tournament eligibility.
 * Constraints (~2,200): 2^12 powers-of-tau sufficient (TeamProof already uses 2^12).
 *
 * C1: passportHash = Poseidon(sessionNullifiers[0..4])
 * C2: All sessionHumanities[i] >= 600 (60% humanity)
 * C3: minHumanityInt <= sessionHumanities[i] for all i (valid lower bound)
 * C4: deviceIdHash = Poseidon(deviceSecret)  (device binding)
 *
 * Public inputs verified on-chain by PITLTournamentPassport.sol:
 *   deviceIdHash, ioidTokenId, passportHash, minHumanityInt, epoch
 */
template TournamentPassport(N) {
    var MIN_HUMANITY = 600;

    signal input deviceIdHash;    // public
    signal input ioidTokenId;     // public
    signal input passportHash;    // public
    signal input minHumanityInt;  // public
    signal input epoch;           // public

    signal input sessionNullifiers[N]; // private
    signal input sessionHumanities[N]; // private
    signal input deviceSecret;         // private

    // C1: passportHash binding
    component pHash = Poseidon(N);
    for (var i = 0; i < N; i++) { pHash.inputs[i] <== sessionNullifiers[i]; }
    pHash.out === passportHash;

    // C2: all sessions meet minimum humanity
    component gte[N];
    for (var i = 0; i < N; i++) {
        gte[i] = GreaterEqThan(10);
        gte[i].in[0] <== sessionHumanities[i];
        gte[i].in[1] <== MIN_HUMANITY;
        gte[i].out === 1;
    }

    // C3: minHumanityInt is valid lower bound
    component lte[N];
    for (var i = 0; i < N; i++) {
        lte[i] = LessEqThan(10);
        lte[i].in[0] <== minHumanityInt;
        lte[i].in[1] <== sessionHumanities[i];
        lte[i].out === 1;
    }

    // C4: device identity binding
    component devHash = Poseidon(1);
    devHash.inputs[0] <== deviceSecret;
    devHash.out === deviceIdHash;
}

component main {public [deviceIdHash, ioidTokenId, passportHash, minHumanityInt, epoch]}
    = TournamentPassport(5);
