"""Priority aging: each completed transaction raises waiting priorities."""

class AgingScheduler:
    def __init__(self, every=1, cap=5):
        if every < 1:
            raise ValueError("Aging interval must be at least 1")
        if cap < 1:
            raise ValueError("Priority cap must be at least 1")
        self.every = every
        self.cap = cap

    def priority(self, base_priority, completed_count):
        base_priority = int(base_priority)
        completed_count = max(0, int(completed_count))
        return min(self.cap, base_priority + completed_count // self.every)
