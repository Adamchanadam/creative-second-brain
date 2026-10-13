"""Deterministic graph neighbourhoods, not semantic similarity or new edges."""
import hashlib
import heapq
import math
from collections import defaultdict


def seed(value):
    return int(hashlib.sha256(value.encode()).hexdigest()[:12], 16)


def arrange(nodes, edges):
    by_id = {n['id']: n for n in nodes}
    adjacency = {key: set() for key in by_id}
    for edge in edges:
        a, b = edge['a'], edge['b']
        if a != b and a in adjacency and b in adjacency:
            adjacency[a].add(b)
            adjacency[b].add(a)
    components, seen = [], set()
    for key in sorted(by_id):
        if key in seen:
            continue
        todo, component = [key], []
        seen.add(key)
        while todo:
            item = todo.pop()
            component.append(item)
            for other in sorted(adjacency[item] - seen):
                seen.add(other)
                todo.append(other)
        components.append(sorted(component))
    owners, distances, anchors = {}, {}, set()
    for component in components:
        if len(component) == 1 and not adjacency[component[0]]:
            owners[component[0]] = 'unlinked'
            distances[component[0]] = None
            continue
        roots = [key for key in component if by_id[key].get('ideaType') == 'hub']
        if not roots:
            # An existing source/title names this connected component, never an invented topic.
            roots = [min(component, key=lambda key: (by_id[key]['kind'] != 'source', -len(adjacency[key]), key))]
        anchors.update(roots)
        queue = [(0, root, root) for root in roots]
        heapq.heapify(queue)
        best = {}
        while queue:
            distance, root, key = heapq.heappop(queue)
            if key in best:
                continue
            best[key] = (distance, root)
            owners[key], distances[key] = root, distance
            for other in sorted(adjacency[key]):
                if other not in best:
                    heapq.heappush(queue, (distance + 1, root, other))
    members = defaultdict(list)
    for key in sorted(by_id):
        members[owners[key]].append(key)
    group_keys = sorted(anchors, key=lambda key: (by_id[key].get('ideaType') != 'hub', -len(members[key]), key))
    centers = {}
    for i, key in enumerate(group_keys):
        angle = i * 2.399963
        radius = 2.8 + 1.13 * math.sqrt(i)
        centers[key] = [math.cos(angle)*radius, math.sin(angle)*radius*.70, math.sin(angle*1.7)*1.15]
    centers['unlinked'] = [0, 0, 0]
    positions, targets = {}, {}
    for key in sorted(by_id):
        group, h = owners[key], seed(key)
        phase = (h % 10000)/10000 * math.tau
        if group == 'unlinked':
            radius = 7.1 + (h % 1000)/1000*.6
            target = [math.cos(phase)*radius, math.sin(phase)*radius*.72, ((h//1000)%1000)/1000*2-1]
            point = target[:]
        else:
            target = centers[group][:]
            radius = 0 if key == group else .38 + min(1.6, .19*math.sqrt(len(members[group]))) * (.35+(h%997)/997*.65)
            point = [target[0]+math.cos(phase)*radius, target[1]+math.sin(phase)*radius, target[2]+(((h//1000)%1000)/1000-.5)*radius]
        targets[key], positions[key] = target, point
    pairs = sorted({tuple(sorted((e['a'], e['b']))) for e in edges if e['a'] != e['b']})
    # Sparse springs plus local collision cells: no all-pairs force or browser simulation.
    offsets = [(x, y, z) for x in (-1, 0, 1) for y in (-1, 0, 1) for z in (-1, 0, 1)] if len(nodes) <= 600 else [(0, 0, 0)]
    for _ in range(100 if len(nodes) <= 600 else 32):
        forces = {key: [(targets[key][j]-p[j])*(.18 if key in anchors else .045) for j in range(3)] for key, p in positions.items()}
        for a, b in pairs:
            delta = [positions[b][j]-positions[a][j] for j in range(3)]
            length = max(.001, math.sqrt(sum(x*x for x in delta)))
            weight = (.045 if owners[a] == owners[b] else .007) / math.sqrt(max(1, min(len(adjacency[a]), len(adjacency[b]))))
            magnitude = (length-.95)*weight/length
            for j in range(3):
                force = delta[j]*magnitude
                forces[a][j] += force
                forces[b][j] -= force
        cells = defaultdict(list)
        for key, p in positions.items():
            cells[tuple(math.floor(x/.85) for x in p)].append(key)
        for a, p in positions.items():
            cell = tuple(math.floor(x/.85) for x in p)
            for x, y, z in offsets:
                neighbours = cells.get((cell[0]+x, cell[1]+y, cell[2]+z), ())
                # Bound dense-cell work in large vaults; full graph springs remain intact.
                if len(nodes) > 600 and len(neighbours) > 16:
                    start = seed(a) % len(neighbours)
                    neighbours = [neighbours[(start+i) % len(neighbours)] for i in range(16)]
                for b in neighbours:
                    if b <= a:
                        continue
                    delta = [p[j]-positions[b][j] for j in range(3)]
                    length = max(.001, math.sqrt(sum(v*v for v in delta)))
                    if length >= .85:
                        continue
                    magnitude = (.85-length)*.32/length
                    for j in range(3):
                        force = delta[j]*magnitude
                        forces[a][j] += force
                        forces[b][j] -= force
        for key, p in positions.items():
            for j in range(3):
                p[j] += max(-.12, min(.12, forces[key][j]))
    extent = max((abs(x) for p in positions.values() for x in p), default=1)
    scale = min(1, 6.3/max(extent, 1))
    groups = []
    for group in group_keys + (['unlinked'] if 'unlinked' in members else []):
        keys = members[group]
        center = [sum(positions[key][j] for key in keys)/len(keys)*scale for j in range(3)]
        radius = max(math.dist([x*scale for x in positions[key]], center) for key in keys)
        groups.append(dict(id=group, title=by_id[group]['title'] if group != 'unlinked' else '尚未連結', count=len(keys), center=center, radius=radius, kind='hub' if group in by_id and by_id[group].get('ideaType') == 'hub' else 'component' if group != 'unlinked' else 'unlinked'))
    for n in nodes:
        n['p'] = [round(x*scale, 6) for x in positions[n['id']]]
        n['group'] = owners[n['id']]
        n['groupHops'] = distances[n['id']]
    return dict(method='explicit-graph-neighbourhoods-v1', groups=groups, meaning='連線鄰近與主題群；非語義相似度分數。')
