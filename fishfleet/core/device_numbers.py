"""Resolve stable unique numbers, prioritising explicit labels over legacy order."""
def resolve_numbers(serials, labels, legacy):
    result = {}
    used = set()
    def valid(n): return type(n) is int and 1 <= n <= 99999
    for source in (labels, legacy):
        for serial in serials:
            if serial in result: continue
            n = source.get(serial, {}).get('index')
            if valid(n) and n not in used:
                result[serial] = n; used.add(n)
    candidate = 1
    for serial in serials:
        if serial in result: continue
        while candidate in used: candidate += 1
        if candidate > 99999: raise ValueError('设备编号已耗尽')
        result[serial] = candidate; used.add(candidate)
    return result
