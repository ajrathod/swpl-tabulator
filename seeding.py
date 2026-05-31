"""Heat seeding logic — pure functions, no DB dependencies."""
import math


def compute_stages(n_skaters, max_per=6, semi_threshold=6, direct_final_max=7):
    """
    Returns structure describing how many heats/semis/finals to create.

    n_skaters:        total number of participants
    max_per:          max per heat (6 for individual, 7 for relay)
    semi_threshold:   only create semis if qualifiers from heats > this number
    direct_final_max: groups with this many or fewer go straight to a single final
    """
    n = n_skaters
    if n == 0:
        return {"heats": 0, "semis": 0, "finals": 1, "adv_heat": 0, "adv_semi": 0}
    if n <= direct_final_max:
        # Straight to final — no heats needed (7 or fewer)
        return {"heats": 0, "semis": 0, "finals": 1, "adv_heat": n, "adv_semi": 0}

    num_heats = math.ceil(n / max_per)

    if num_heats <= 2:
        adv_heat = 3
        total_qualifiers = adv_heat * num_heats   # e.g. 3×2 = 6
        need_semi = total_qualifiers > semi_threshold
        if need_semi:
            return {"heats": num_heats, "semis": 2, "finals": 1,
                    "adv_heat": adv_heat, "adv_semi": 3}
        else:
            return {"heats": num_heats, "semis": 0, "finals": 1,
                    "adv_heat": adv_heat, "adv_semi": 0}
    else:
        adv_heat = math.ceil(12 / num_heats)
        return {"heats": num_heats, "semis": 2, "finals": 1,
                "adv_heat": adv_heat, "adv_semi": 3}


def distribute(panels, num_buckets):
    """Evenly distribute panels into num_buckets lists."""
    if num_buckets <= 0:
        return [panels]
    size = math.ceil(len(panels) / num_buckets)
    return [panels[i*size:(i+1)*size] for i in range(num_buckets)]
