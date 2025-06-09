import json
import pytest
import types
from balaambot.cats.cat_handler import CatHandler
from balaambot.cats import cat_handler

GUILD_ID = 12345

@pytest.fixture
def patch_save_file(monkeypatch, tmp_path):
    # Patch SAVE_FILE to a temp file
    save_file = tmp_path / "cats.json"
    monkeypatch.setattr(cat_handler, "SAVE_FILE", save_file)
    return save_file

@pytest.fixture
def patch_logger(monkeypatch):
    dummy_logger = types.SimpleNamespace(
        info=lambda *a, **k: None,
        exception=lambda *a, **k: None,
    )
    monkeypatch.setattr(cat_handler, "logger", dummy_logger)
    return dummy_logger

def test_init_no_file(patch_save_file, patch_logger):
    handler = CatHandler()
    assert handler.get_num_cats(GUILD_ID) == 0

def test_init_with_valid_file(patch_save_file, patch_logger):
    owner_id = 123456
    cats_data = {"guild_cats": {str(GUILD_ID): {"mittens": {"name": "Mittens", "owner": owner_id}}}}
    patch_save_file.write_text(json.dumps(cats_data))
    handler = CatHandler()
    assert "mittens" in handler.db.guild_cats.get(GUILD_ID, {})
    cat_obj = handler.db.guild_cats[GUILD_ID]["mittens"]
    assert isinstance(cat_obj, cat_handler.Cat)
    assert cat_obj.name == "Mittens"
    assert cat_obj.owner == owner_id

def test_init_with_invalid_json(patch_save_file, patch_logger, monkeypatch):
    logged = {}
    def fake_exception(msg, *a, **k): logged["called"] = True
    monkeypatch.setattr(cat_handler, "logger", types.SimpleNamespace(
        info=lambda *a, **k: None,
        exception=fake_exception,
    ))
    patch_save_file.write_text("{not valid json")
    handler = CatHandler()
    assert logged.get("called")

def test_add_cat_creates_and_persists(patch_save_file, patch_logger):
    handler = CatHandler()
    owner_id = 555
    handler.add_cat("Whiskers", GUILD_ID, owner_id)
    cat_obj = handler.db.guild_cats[GUILD_ID].get("whiskers")
    assert isinstance(cat_obj, cat_handler.Cat)
    assert cat_obj.name == "Whiskers"
    assert cat_obj.owner == owner_id
    assert handler.get_cat("whiskers", GUILD_ID) == "Whiskers"
    # File should exist and contain the cat
    with patch_save_file.open() as f:
        data = json.load(f)
    assert "guild_cats" in data
    assert str(GUILD_ID) in data["guild_cats"]
    assert "whiskers" in data["guild_cats"][str(GUILD_ID)]
    assert data["guild_cats"][str(GUILD_ID)]["whiskers"]["name"] == "Whiskers"
    assert data["guild_cats"][str(GUILD_ID)]["whiskers"]["owner"] == owner_id

def test_add_cat_normalizes_id(patch_save_file, patch_logger):
    handler = CatHandler()
    owner_id = 123
    handler.add_cat("  Fluffy  ", GUILD_ID, owner_id)
    # All these should return the original name as stored
    assert handler.get_cat("fluffy", GUILD_ID) == "  Fluffy  "
    assert handler.get_cat("  FLUFFY", GUILD_ID) == "  Fluffy  "
    assert handler.get_cat("FlUfFy", GUILD_ID) == "  Fluffy  "
    assert handler.get_cat("other", GUILD_ID) is None
    # Owner is correct
    cat_obj = handler.db.guild_cats[GUILD_ID]["fluffy"]
    assert cat_obj.owner == owner_id

def test_get_cat_names(patch_save_file, patch_logger):
    handler = CatHandler()
    owner1 = 1
    owner2 = 2
    handler.add_cat("A", GUILD_ID, owner1)
    handler.add_cat("B", GUILD_ID, owner2)
    names = handler.get_cat_names(GUILD_ID)
    assert f"- A (Owner: <@{owner1}>)" in names
    assert f"- B (Owner: <@{owner2}>)" in names
    assert names.count("- ") == 2

def test_get_num_cats(patch_save_file, patch_logger):
    handler = CatHandler()
    assert handler.get_num_cats(GUILD_ID) == 0
    handler.add_cat("X", GUILD_ID, 1)
    handler.add_cat("Y", GUILD_ID, 2)
    assert handler.get_num_cats(GUILD_ID) == 2

def test_save_creates_file_if_missing(patch_save_file, patch_logger):
    handler = CatHandler()
    handler.add_cat("Zed", GUILD_ID, 42)
    assert patch_save_file.exists()
    with patch_save_file.open() as f:
        data = json.load(f)
    assert "guild_cats" in data
    assert str(GUILD_ID) in data["guild_cats"]
    assert "zed" in data["guild_cats"][str(GUILD_ID)]
    assert data["guild_cats"][str(GUILD_ID)]["zed"]["name"] == "Zed"
    assert data["guild_cats"][str(GUILD_ID)]["zed"]["owner"] == 42

def test_get_cat_id_normalization(patch_save_file, patch_logger):
    handler = CatHandler()
    assert handler._get_cat_id("  Foo  ") == "foo"
    assert handler._get_cat_id("BAR") == "bar"

def test_cats_are_isolated_by_guild(patch_save_file, patch_logger):
    handler = CatHandler()
    guild1 = 111
    guild2 = 222
    handler.add_cat("Mittens", guild1, 1)
    handler.add_cat("Fluffy", guild2, 2)
    # Each guild only sees its own cats
    assert handler.get_cat("Mittens", guild1) == "Mittens"
    assert handler.get_cat("Fluffy", guild1) is None
    assert handler.get_cat("Fluffy", guild2) == "Fluffy"
    assert handler.get_cat("Mittens", guild2) is None
    # Names list is correct per guild
    assert "- Mittens" in handler.get_cat_names(guild1)
    assert "- Fluffy" not in handler.get_cat_names(guild1)
    assert "- Fluffy" in handler.get_cat_names(guild2)
    assert "- Mittens" not in handler.get_cat_names(guild2)
    # Counts are correct
    assert handler.get_num_cats(guild1) == 1
    assert handler.get_num_cats(guild2) == 1

def test_guilds_are_persisted_separately(patch_save_file, patch_logger):
    handler = CatHandler()
    guild1 = 333
    guild2 = 444
    handler.add_cat("Tiger", guild1, 10)
    handler.add_cat("Shadow", guild2, 20)
    # Save and reload
    handler2 = CatHandler()
    assert handler2.get_cat("Tiger", guild1) == "Tiger"
    assert handler2.get_cat("Shadow", guild2) == "Shadow"
    assert handler2.get_cat("Tiger", guild2) is None
    assert handler2.get_cat("Shadow", guild1) is None

def test_get_cat_returns_none_if_guild_missing(patch_save_file, patch_logger):
    handler = CatHandler()
    # Use a guild_id that does not exist
    cat = handler.get_cat("anycat", 99999)
    assert cat is None

def test_remove_cat_success(patch_save_file, patch_logger):
    handler = CatHandler()
    owner_id = 123
    handler.add_cat("Whiskers", GUILD_ID, owner_id)
    # Remove as owner
    success, msg = handler.remove_cat("Whiskers", GUILD_ID, owner_id)
    assert success is True
    assert "removed" in msg
    # Cat is gone
    assert handler.get_cat("Whiskers", GUILD_ID) is None

def test_remove_cat_not_owner(patch_save_file, patch_logger):
    handler = CatHandler()
    owner_id = 123
    other_id = 456
    handler.add_cat("Whiskers", GUILD_ID, owner_id)
    # Try to remove as non-owner
    success, msg = handler.remove_cat("Whiskers", GUILD_ID, other_id)
    assert success is False
    assert "not the owner" in msg
    # Cat still exists
    assert handler.get_cat("Whiskers", GUILD_ID) == "Whiskers"

def test_remove_cat_not_exist(patch_save_file, patch_logger):
    handler = CatHandler()
    # Try to remove a cat that doesn't exist
    success, msg = handler.remove_cat("Ghost", GUILD_ID, 123)
    assert success is False
    assert "No cat named Ghost exists" in msg
