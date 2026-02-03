from typing import List


def select_default_controller_type_index(controller_types: List[str]) -> int:
    if "generic" in controller_types:
        return controller_types.index("generic")
    return 0
