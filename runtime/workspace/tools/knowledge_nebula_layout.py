"""Deterministic graph neighbourhoods, not semantic similarity or new edges."""
import hashlib
import heapq
import math
from collections import defaultdict, deque

# Groups with more members than this get a second level of sub-groups of about
# SUBGROUP_SIZE members each. The group root stays at the group centre, outside them.
SUBGROUP_MIN = 48
SUBGROUP_SIZE = 36
GROUP_GAP = 1.2         # centre distance between two groups, as a share of their summed radii
SUBGROUP_RING = .85     # sub-group ring radius, as a share of the parent's scatter radius
SUBGROUP_PULL = .09     # pull towards the sub-group centre (a plain group member: .045)
SUBGROUP_CROSS = .35    # spring weight kept on links that cross sub-groups of one group
# New stars (kind 'thought', one per thinking record) are placed after the graph layout and take no part in it, so a vault
# with or without thinking records gives every other node exactly the same position.
THOUGHT_KIND = 'thought'
THOUGHT_OFFSET = .35      # distance of a new star from the centre of its parent stars (world units); the direction comes from its id
THOUGHT_OUTER_GAP = .8    # a new star without a parent star sits this far outside the outermost placed node...
THOUGHT_OUTER_SPREAD = .4 # ...plus up to this much more, chosen by its id
THOUGHT_RING_SQUASH = .72 # the ring is flattened like the ring of unlinked nodes: y is this share of x


def seed(value):
    return int(hashlib.sha256(value.encode()).hexdigest()[:12], 16)


def place_thoughts(thoughts, graph):
    """New stars: base position = centre of the parent stars' base positions plus an id-seeded offset of THOUGHT_OFFSET;
    without a parent, on an outer ring (also by id). The group is the one most parents belong to (ties: the smaller group
    id), else 'unlinked'; group counts, centres and radii are the graph's and stay as they were."""
    by_id = {n['id']: n for n in graph}
    outer = max((math.hypot(n['p'][0], n['p'][1]/THOUGHT_RING_SQUASH) for n in graph), default=0)
    for star in sorted(thoughts, key=lambda n: n['id']):
        parents = [by_id[key] for key in dict.fromkeys(star.get('parents') or []) if key in by_id]
        h = seed(star['id'])
        azimuth = (h % 10000)/10000*math.tau
        height = ((h//10000) % 1000)/1000*2-1
        flat = math.sqrt(1-height*height)
        if parents:
            centre = [sum(p['p'][j] for p in parents)/len(parents) for j in range(3)]
            direction = [math.cos(azimuth)*flat, math.sin(azimuth)*flat, height]
            star['p'] = [round(centre[j]+direction[j]*THOUGHT_OFFSET, 6) for j in range(3)]
            counts = {}
            for p in parents:
                if p['group'] != 'unlinked':
                    counts[p['group']] = counts.get(p['group'], 0)+1
            group = min(counts, key=lambda g: (-counts[g], g)) if counts else 'unlinked'
            subs = {}
            for p in parents:
                if p['group'] == group and p.get('subgroup'):
                    subs[p['subgroup']] = subs.get(p['subgroup'], 0)+1
            star['group'], star['subgroup'] = group, (min(subs, key=lambda s: (-subs[s], s)) if subs else None)
        else:
            radius = outer+THOUGHT_OUTER_GAP+(h % 1000)/1000*THOUGHT_OUTER_SPREAD
            star['p'] = [round(math.cos(azimuth)*radius, 6), round(math.sin(azimuth)*radius*THOUGHT_RING_SQUASH, 6), round(height*.5, 6)]
            star['group'], star['subgroup'] = 'unlinked', None
        star['groupHops'] = None


def scatter_radius(count):
    """Initial scatter of a group or sub-group; grows with sqrt(count), never capped."""
    return .38 + .19*math.sqrt(count)


def arrange(nodes, edges):
    thoughts = [n for n in nodes if n.get('kind') == THOUGHT_KIND]
    if thoughts:
        # The graph is laid out exactly as it would be without the new stars (their lines are ignored too); the stars come last.
        ids = {n['id'] for n in thoughts}
        graph = [n for n in nodes if n['id'] not in ids]
        result = arrange(graph, [e for e in edges if e['a'] not in ids and e['b'] not in ids])
        place_thoughts(thoughts, graph)
        return result
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
    semantic = lambda key: by_id[key].get('semanticDegree', len(adjacency[key]))
    # Second level: only large groups are split; anchors are the best-connected non-hub
    # members and every member joins the nearest anchor by connection hops inside its group.
    subgroup_of, sub_members, sub_anchor, sub_parent = {}, {}, {}, {}
    for group in group_keys:
        keys = members[group]
        if len(keys) <= SUBGROUP_MIN:
            continue
        pool = sorted((key for key in keys if key != group and by_id[key].get('ideaType') != 'hub'), key=lambda key: (-semantic(key), key))
        chosen = pool[:math.ceil(len(keys)/SUBGROUP_SIZE)]
        if len(chosen) < 2:
            continue
        inside, hops = set(keys), {}
        for anchor in chosen:
            reached, todo = {anchor: 0}, deque([anchor])
            while todo:
                item = todo.popleft()
                for other in sorted(adjacency[item] & inside):
                    if other not in reached:
                        reached[other] = reached[item] + 1
                        todo.append(other)
            hops[anchor] = reached
        for key in keys:
            if key == group:
                continue  # the group root stays at the group centre
            best = min(chosen, key=lambda anchor: (hops[anchor].get(key, math.inf), anchor))
            sid = group + '>' + best
            subgroup_of[key] = sid
            sub_members.setdefault(sid, []).append(key)
            sub_anchor[sid] = best
            sub_parent[sid] = group
    # Group centres: golden-angle spiral, pushed outwards until neighbours no longer overlap,
    # so spacing follows the sqrt(count) scatter radii instead of a fixed ring.
    group_radius = {key: scatter_radius(len(members[key])) for key in group_keys}
    centers, placed = {}, []
    for i, key in enumerate(group_keys):
        angle, radius = i * 2.399963, 2.8 + 1.13 * math.sqrt(i)
        while True:
            point = [math.cos(angle)*radius, math.sin(angle)*radius*.70, math.sin(angle*1.7)*1.15]
            if all(math.dist(point, other) >= GROUP_GAP*(group_radius[key]+group_radius[name]) for name, other in placed):
                break
            radius += .25
        placed.append((key, point))
    if placed:
        total = sum(len(members[key]) for key, _ in placed)
        middle = [sum(p[j]*len(members[key]) for key, p in placed)/total for j in range(3)]
        for key, point in placed:
            centers[key] = [point[j]-middle[j] for j in range(3)]
    outer = max((math.sqrt(sum(x*x for x in centers[key])) + group_radius[key] for key in group_keys), default=0)
    centers['unlinked'] = [0, 0, 0]
    sub_centers = {}
    for group in group_keys:
        siblings = sorted((sid for sid in sub_members if sub_parent[sid] == group), key=lambda sid: (-len(sub_members[sid]), sid))
        phase = seed(group) % 1000 / 1000 * math.tau
        for j, sid in enumerate(siblings):
            angle = phase + math.tau * j / len(siblings)
            ring = SUBGROUP_RING * group_radius[group]
            c = centers[group]
            sub_centers[sid] = [c[0]+math.cos(angle)*ring, c[1]+math.sin(angle)*ring, c[2]+math.sin(angle*1.7)*ring*.35]
    positions, targets = {}, {}
    for key in sorted(by_id):
        group, h = owners[key], seed(key)
        phase = (h % 10000)/10000 * math.tau
        if group == 'unlinked':
            radius = max(7.1, outer + .8) + (h % 1000)/1000*.6
            target = [math.cos(phase)*radius, math.sin(phase)*radius*.72, ((h//1000)%1000)/1000*2-1]
            point = target[:]
        else:
            sid = subgroup_of.get(key)
            if key == group:
                target, radius = centers[group][:], 0
            elif sid:
                target = sub_centers[sid][:]
                radius = scatter_radius(len(sub_members[sid])) * (.35+(h%997)/997*.65)
            else:
                target = centers[group][:]
                radius = scatter_radius(len(members[group])) * (.35+(h%997)/997*.65)
            point = [target[0]+math.cos(phase)*radius, target[1]+math.sin(phase)*radius, target[2]+(((h//1000)%1000)/1000-.5)*radius]
        targets[key], positions[key] = target, point
    pairs = sorted({tuple(sorted((e['a'], e['b']))) for e in edges if e['a'] != e['b']})
    # Sparse springs plus local collision cells: no all-pairs force or browser simulation.
    offsets = [(x, y, z) for x in (-1, 0, 1) for y in (-1, 0, 1) for z in (-1, 0, 1)] if len(nodes) <= 600 else [(0, 0, 0)]
    for _ in range(100 if len(nodes) <= 600 else 32):
        forces = {key: [(targets[key][j]-p[j])*(.18 if key in anchors else SUBGROUP_PULL if key in subgroup_of else .045) for j in range(3)] for key, p in positions.items()}
        for a, b in pairs:
            delta = [positions[b][j]-positions[a][j] for j in range(3)]
            length = max(.001, math.sqrt(sum(x*x for x in delta)))
            weight = (.045 if owners[a] == owners[b] else .007) / math.sqrt(max(1, min(len(adjacency[a]), len(adjacency[b]))))
            if owners[a] == owners[b] and subgroup_of.get(a) != subgroup_of.get(b):
                weight *= SUBGROUP_CROSS
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
    # Unlinked nodes sit just outside the settled graph, so a large graph is not shrunk to fit a fixed ring.
    reach = max((math.hypot(p[0], p[1]) for key, p in positions.items() if owners[key] != 'unlinked'), default=0)
    for key, p in positions.items():
        if owners[key] == 'unlinked':
            spin = math.hypot(p[0], p[1]) or 1
            wanted = max(7.1, reach + .8) + (seed(key) % 1000)/1000*.6
            p[0], p[1] = p[0]/spin*wanted, p[1]/spin*wanted
    extent = max((abs(x) for p in positions.values() for x in p), default=1)
    scale = min(1, 6.3/max(extent, 1))
    groups = []
    for group in group_keys + (['unlinked'] if 'unlinked' in members else []):
        keys = members[group]
        center = [sum(positions[key][j] for key in keys)/len(keys)*scale for j in range(3)]
        radius = max(math.dist([x*scale for x in positions[key]], center) for key in keys)
        groups.append(dict(id=group, title=by_id[group]['title'] if group != 'unlinked' else '尚未連結', count=len(keys), center=center, radius=radius, kind='hub' if group in by_id and by_id[group].get('ideaType') == 'hub' else 'component' if group != 'unlinked' else 'unlinked'))
        for sid in sorted((sid for sid in sub_members if sub_parent[sid] == group), key=lambda sid: (-len(sub_members[sid]), sid)):
            subs = sub_members[sid]
            middle = [sum(positions[key][j] for key in subs)/len(subs)*scale for j in range(3)]
            reach = max(math.dist([x*scale for x in positions[key]], middle) for key in subs)
            groups.append(dict(id=sid, parent=group, title=by_id[sub_anchor[sid]]['title'], count=len(subs), center=middle, radius=reach, kind='subgroup'))
    for n in nodes:
        n['p'] = [round(x*scale, 6) for x in positions[n['id']]]
        n['group'] = owners[n['id']]
        n['groupHops'] = distances[n['id']]
        n['subgroup'] = subgroup_of.get(n['id'])
    return dict(method='explicit-graph-neighbourhoods-v1', groups=groups, meaning='連線鄰近與主題群；非語義相似度分數。')
