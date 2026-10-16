"""Wait-for graph utilities independent of the all-or-nothing lock manager."""


def find_cycles(graph):
    """Return directed cycles in a mapping of node -> iterable of wait-for nodes."""
    cycles = []
    visiting = []
    active = set()
    done = set()

    def visit(node):
        if node in active:
            start = visiting.index(node)
            cycle = visiting[start:] + [node]
            canonical = tuple(cycle[:-1])
            # Deduplicate rotations of the same cycle.
            rotations = [canonical[i:] + canonical[:i] for i in range(len(canonical))]
            key = min(rotations, key=lambda item: tuple(map(str, item)))
            if key not in seen:
                seen.add(key)
                cycles.append(list(key) + [key[0]])
            return
        if node in done:
            return
        active.add(node)
        visiting.append(node)
        for nxt in graph.get(node, ()):
            if nxt in graph:
                visit(nxt)
        visiting.pop()
        active.remove(node)
        done.add(node)

    seen = set()
    for node in graph:
        visit(node)
    return cycles


def has_cycle(graph):
    """Return True when the wait-for graph contains at least one cycle."""
    return bool(find_cycles(graph))
