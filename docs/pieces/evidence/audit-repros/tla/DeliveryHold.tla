---------------------------- MODULE DeliveryHold ----------------------------
(* Delivery.tla plus Hold/Release as the proposal words it: a held message is logged without the agent in
   `recipients` (so not in pending, and hidden from its reads by `visible`), and a later release fills the inbox.
   Marks and the delivered `upto` stay keyed on the message's own seq, as team.py has them. *)
EXTENDS Naturals, Sequences, FiniteSets
CONSTANTS N, K
VARIABLES addressed, pending, mark, delivered, seen, phase, held
vars == <<addressed, pending, mark, delivered, seen, phase, held>>
Idx == 1..N
Max(S) == CHOOSE x \in S : \A y \in S : y <= x
Reasons == {i \in pending : i > mark}
Oldest(S) == CHOOSE T \in SUBSET S :
    /\ Cardinality(T) = IF Cardinality(S) < K THEN Cardinality(S) ELSE K
    /\ \A t \in T, u \in S \ T : t < u
MarkAfter(shown) == Max({mark} \cup {i \in shown : \A j \in (mark + 1)..i : j \in shown \/ j \in held})
Deliver(shown) ==
    /\ delivered' = [i \in Idx |-> IF i \in shown THEN delivered[i] + 1 ELSE delivered[i]]
    /\ seen' = [i \in Idx |-> IF i \in shown THEN seen[i] + 1 ELSE seen[i]]
    /\ pending' = {p \in pending : p > Max(shown)}
    /\ mark' = MarkAfter(shown)
Init == /\ addressed = <<>> /\ pending = {} /\ mark = 0 /\ held = {}
        /\ delivered = [i \in Idx |-> 0] /\ seen = [i \in Idx |-> 0] /\ phase = "turn"
TeammatePosts ==
    /\ Len(addressed) < N
    /\ \E a \in BOOLEAN, h \in BOOLEAN :
        /\ addressed' = Append(addressed, a)
        /\ pending' = IF a /\ ~h THEN pending \cup {Len(addressed) + 1} ELSE pending
        /\ held' = IF a /\ h THEN held \cup {Len(addressed) + 1} ELSE held
    /\ UNCHANGED <<mark, delivered, seen, phase>>
Release == /\ held # {} /\ pending' = pending \cup held /\ held' = {}
           /\ UNCHANGED <<addressed, mark, delivered, seen, phase>>
HookDelivers == /\ phase = "turn" /\ Reasons # {} /\ Deliver(Oldest(Reasons)) /\ UNCHANGED <<addressed, phase, held>>
Vis == {i \in (mark + 1)..Len(addressed) : i \notin held}
AgentReads == /\ phase = "turn" /\ Vis # {}
              /\ seen' = [i \in Idx |-> IF i \in Vis THEN seen[i] + 1 ELSE seen[i]]
              /\ mark' = Max(Vis)
              /\ UNCHANGED <<addressed, pending, delivered, phase, held>>
TurnEnds == phase = "turn" /\ phase' = "between" /\ UNCHANGED <<addressed, pending, mark, delivered, seen, held>>
NextTurnStarts == /\ phase = "between" /\ phase' = "turn"
                  /\ IF Reasons = {} THEN UNCHANGED <<pending, mark, delivered, seen>> ELSE Deliver(Oldest(Reasons))
                  /\ UNCHANGED <<addressed, held>>
Next == TeammatePosts \/ Release \/ HookDelivers \/ AgentReads \/ TurnEnds \/ NextTurnStarts
Spec == Init /\ [][Next]_vars /\ WF_vars(TurnEnds) /\ WF_vars(NextTurnStarts) /\ WF_vars(Release)
DeliveredOnce == \A i \in Idx : delivered[i] <= 1
ReleasedNotDropped == \A i \in Idx : (i <= Len(addressed) /\ addressed[i] /\ i \notin held /\ i \notin pending) => seen[i] >= 1
HeldArrives == \A i \in Idx : [](i \in held => <>(seen[i] >= 1))
=============================================================================
