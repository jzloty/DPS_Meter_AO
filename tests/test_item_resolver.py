from __future__ import annotations

from pathlib import Path

from albion_dps.domain.item_resolver import load_item_resolver


def test_item_resolver_role_from_items_json(tmp_path: Path) -> None:
    indexed_path = tmp_path / "indexedItems.json"
    items_path = tmp_path / "items.json"

    indexed_path.write_text(
        '[{"Index":"1","UniqueName":"T4_2H_HOLYSTAFF"},{"Index":"2","UniqueName":"T4_MAIN_SWORD"}]',
        encoding="utf-8",
    )
    items_path.write_text(
        '[{"UniqueName":"T4_2H_HOLYSTAFF","shopsubcategory1":"holystaff"},{"UniqueName":"T4_MAIN_SWORD","shopsubcategory1":"sword"}]',
        encoding="utf-8",
    )

    resolver = load_item_resolver(indexed_path=indexed_path, items_path=items_path)
    assert resolver.role_for_items([1]) == "heal"
    assert resolver.role_for_items([2]) == "dps"


def test_item_resolver_role_from_unique_pattern(tmp_path: Path) -> None:
    indexed_path = tmp_path / "indexedItems.json"
    indexed_path.write_text(
        '[{"Index":"5","UniqueName":"T4_2H_ARCANESTAFF"}]',
        encoding="utf-8",
    )

    resolver = load_item_resolver(indexed_path=indexed_path, items_path=None)
    assert resolver.role_for_items([5]) == "tank"


def test_item_resolver_role_from_category_mapping_py(tmp_path: Path) -> None:
    indexed_path = tmp_path / "indexedItems.json"
    mapping_path = tmp_path / "item_category_mapping.py"

    indexed_path.write_text(
        '[{"Index":"7","UniqueName":"T4_MAIN_MACE"}]',
        encoding="utf-8",
    )
    mapping_path.write_text(
        "mapping = {'T4_MAIN_MACE': 'MACE'}\n",
        encoding="utf-8",
    )

    resolver = load_item_resolver(
        indexed_path=indexed_path,
        items_path=None,
        category_path=mapping_path,
    )
    assert resolver.role_for_items([7]) == "tank"
