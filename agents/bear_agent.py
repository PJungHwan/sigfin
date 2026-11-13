"""하락 근거가 있을 때만 주장하고 상대 상승 근거도 인정하는 Mock 어댑터."""

from agents.debate import initial_position, rebuttals


class BearAgent:
    def propose(self, evidence):
        return initial_position(evidence, "BEAR")

    def respond(self, evidence, opponent):
        return rebuttals(evidence, "BEAR", opponent)
