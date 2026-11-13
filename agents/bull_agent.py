"""상승 근거가 있을 때만 주장하고 상대 하락 근거도 인정하는 Mock 어댑터."""

from agents.debate import initial_position, rebuttals


class BullAgent:
    def propose(self, evidence):
        return initial_position(evidence, "BULL")

    def respond(self, evidence, opponent):
        return rebuttals(evidence, "BULL", opponent)
