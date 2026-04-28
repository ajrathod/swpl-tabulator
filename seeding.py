"""Heat seeding logic — pure functions, no DB dependencies."""
import math


def compute_stages(n_skaters, max_per=6):
    """
    Returns structure describing how many heats/semis/finals to create.
    n_skaters: int
    Returns: {"heats": int, "semis": int, "finals": 1,
              "adv_heat": int, "adv_semi": int}
    """
    n = n_skaters
    if n == 0:
        return {"heats": 0, "semis": 0, "finals": 1, "adv_heat": 0, "adv_semi": 0}
    if n <= max_per:
        return {"heats": 0, "semis": 0, "finals": 1, "adv_heat": n, "adv_semi": 0}

    num_heats = math.ceil(n / max_per)

    if num_heats <= 2:
        return {"heats": num_heats, "semis": 0, "finals": 1,
                "adv_heat": 3, "adv_semi": 0}
    else:
        adv = math.ceil(12 / num_heats)
        return {"heats": num_heats, "semis": 2, "finals": 1,
                "adv_heat": adv, "adv_semi": 3}


def distribute(panels, num_buckets):
    """Evenly distribute panels into num_buckets lists."""
    if num_buckets <= 0:
        return [panels]
    size = math.ceil(len(panels) / num_buckets)
    return [panels[i*size:(i+1)*size] for i in range(num_buckets)]
