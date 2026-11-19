---------------------------- MODULE Delivery ----------------------------
(* How messages reach one agent, as swarmory/control_plane/team.py delivers them: between tool calls (Team.inbox,
   which Claude Code's hook calls) and at the start of each turn (Team.wait). Teammates post into one conversation
   the agent is in; a message is addressed to the agent or not. The agent can also read the conversation itself
   (Team.read). Each delivery shows at most K of the messages for the agent, oldest first (Team.undelivered), counts them
   delivered (the "delivered" and "turn" events), and moves the read mark (Team.mark_read). *)
EXTENDS Naturals, Sequences, FiniteSets

CONSTANTS N, K  \* how many messages teammates post; how many one delivery shows (INBOX_MOST, TURN_MOST)

VARIABLES
    addressed,  \* the posted messages in order: TRUE when the message is for the agent
    pending,    \* messages for the agent not yet counted delivered (Team.inboxes)
    mark,       \* the agent's read mark in the conversation (Team.marks)
    delivered,  \* how many times a delivery showed each message
    seen,       \* how many times the agent saw each message, by a delivery or by reading
    phase       \* "turn": a session runs; "between": the worker holds in `swarmory next`

vars == <<addressed, pending, mark, delivered, seen, phase>>
Idx == 1..N
Max(S) == CHOOSE x \in S : \A y \in S : y <= x

Reasons == {i \in pending : i > mark}  \* Team.undelivered: messages for the agent not yet shown or read

Oldest(S) == CHOOSE T \in SUBSET S :
    /\ Cardinality(T) = IF Cardinality(S) < K THEN Cardinality(S) ELSE K
    /\ \A t \in T, u \in S \ T : t < u

(* Team.mark_read: the mark moves to the last shown message before the first unread one that was not shown. *)
MarkAfter(shown) ==
    Max({mark} \cup {i \in shown : \A j \in (mark + 1)..i : j \in shown})

Deliver(shown) ==
    /\ delivered' = [i \in Idx |-> IF i \in shown THEN delivered[i] + 1 ELSE delivered[i]]
    /\ seen' = [i \in Idx |-> IF i \in shown THEN seen[i] + 1 ELSE seen[i]]
    /\ pending' = {p \in pending : p > Max(shown)}  \* the event's upto: the last message shown
    /\ mark' = MarkAfter(shown)

Init ==
    /\ addressed = <<>>
    /\ pending = {}
    /\ mark = 0
    /\ delivered = [i \in Idx |-> 0]
    /\ seen = [i \in Idx |-> 0]
    /\ phase = "turn"

TeammatePosts ==
    /\ Len(addressed) < N
    /\ \E a \in BOOLEAN :
        /\ addressed' = Append(addressed, a)
        /\ pending' = IF a THEN pending \cup {Len(addressed) + 1} ELSE pending
    /\ UNCHANGED <<mark, delivered, seen, phase>>

HookDelivers ==  \* Team.inbox, after a tool call
    /\ phase = "turn"
    /\ Reasons # {}
    /\ Deliver(Oldest(Reasons))
    /\ UNCHANGED <<addressed, phase>>

AgentReads ==  \* Team.read: every unread message, marked read
    /\ phase = "turn"
    /\ mark < Len(addressed)
    /\ seen' = [i \in Idx |-> IF mark < i /\ i <= Len(addressed) THEN seen[i] + 1 ELSE seen[i]]
    /\ mark' = Len(addressed)
    /\ UNCHANGED <<addressed, pending, delivered, phase>>

TurnEnds ==
    /\ phase = "turn"
    /\ phase' = "between"
    /\ UNCHANGED <<addressed, pending, mark, delivered, seen>>

NextTurnStarts ==  \* Team.next: at once, or when a waiting agent gets a message or has waited IDLE seconds
    /\ phase = "between"
    /\ phase' = "turn"
    /\ IF Reasons = {} THEN UNCHANGED <<pending, mark, delivered, seen>> ELSE Deliver(Oldest(Reasons))
    /\ UNCHANGED addressed

Next == TeammatePosts \/ HookDelivers \/ AgentReads \/ TurnEnds \/ NextTurnStarts

Spec == Init /\ [][Next]_vars /\ WF_vars(TurnEnds) /\ WF_vars(NextTurnStarts)

(* No delivery shows a message twice. *)
DeliveredOnce == \A i \in Idx : delivered[i] <= 1

(* A message counts as read only once the agent saw it: a delivery never hides a message it left out. *)
ReadMeansSeen == \A i \in 1..mark : seen[i] >= 1

(* Every message for the agent reaches it, by a delivery or by reading. *)
EachMessageArrives == \A i \in Idx : [](i \in pending => <>(seen[i] >= 1))
=============================================================================
