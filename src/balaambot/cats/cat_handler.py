import logging
import pathlib

import pydantic

import balaambot.config

logger = logging.getLogger(__name__)

SAVE_FILE = pathlib.Path(balaambot.config.PERSISTENT_DATA_DIR) / "cats.json"

# TODOs:
# fuzzy search for cat names (try pkg: the fuzz)
# move message strings to separate file (cat_commands_strings.py?)


class Cat(pydantic.BaseModel):
    """Data representing a cat."""

    name: str
    owner: int  # Discord user ID of the owner


class CatData(pydantic.BaseModel):
    """Data class holding cats indexed by Discord guild ID and cat name."""

    guild_cats: dict[int, dict[str, Cat]]


class CatHandler:
    """Main class for handling cat interactions."""

    def __init__(self) -> None:
        """Initialize the CatHandler."""
        self.db = self._load_cat_db()

    def get_num_cats(self, guild_id: int) -> int:
        """How many cats there are.

        Args:
            guild_id (int): The Discord guild to check

        Returns:
            int: Number of cats

        """
        return len(self.db.guild_cats.get(guild_id, {}))

    def get_cat(self, cat_name: str, guild_id: int) -> str | None:
        """Check if cat exists and return their name if they do.

        Args:
            cat_name (str): Cat name to check
            guild_id (int): The Discord guild to check

        Returns:
            str: The cat's official name
            None: Cat doesn't exist

        """
        cats = self.db.guild_cats.get(guild_id)
        if not cats:
            return None
        cat = cats.get(self._get_cat_id(cat_name))
        return cat.name if cat else None

    def get_cat_names(self, guild_id: int) -> str:
        """Get a formatted list of cat names and owners."""
        return "\n".join(
            f"- {cat.name} (Owner: <@{cat.owner}>)"
            for cat in self.db.guild_cats.get(guild_id, {}).values()
        )

    def add_cat(self, cat_name: str, guild_id: int, owner_id: int) -> None:
        """Creates a new cat.

        Args:
            cat_name (str): The name of the cat to create
            guild_id (int): The Discord guild to create them in
            owner_id (int): The Discord user ID of the owner

        """
        cat_id = self._get_cat_id(cat_name)
        # Make a new cat and save it
        if guild_id not in self.db.guild_cats:
            self.db.guild_cats[guild_id] = {}
        self.db.guild_cats[guild_id][cat_id] = Cat(name=cat_name, owner=owner_id)
        self._save_cat_db(self.db)

    def remove_cat(
        self, cat_name: str, guild_id: int, user_id: int
    ) -> tuple[bool, str]:
        """Remove a cat if the user is the owner.

        Args:
            cat_name (str): The name of the cat to remove
            guild_id (int): The Discord guild to remove from
            user_id (int): The Discord user ID of the user requesting removal

        Returns:
            tuple[bool, str]: (success, message)

        """
        cat_id = self._get_cat_id(cat_name)
        cats = self.db.guild_cats.get(guild_id, {})
        cat_obj = cats.get(cat_id)
        if not cat_obj:
            return False, f"No cat named {cat_name} exists."
        if cat_obj.owner != user_id:
            return (
                False,
                f"You are not the owner of {cat_obj.name}. "
                "Only the owner can remove this cat. :pouting_cat:",
            )
        del cats[cat_id]
        self.db.guild_cats[guild_id] = cats
        self._save_cat_db(self.db)
        return (
            True,
            (
                f"{cat_obj.name} has been removed from the server. "
                "Goodbye! :crying_cat_face:"
            ),
        )

    def _get_cat_id(self, cat_name: str) -> str:
        return cat_name.strip().lower()

    def _load_cat_db(self) -> CatData:
        """Load cats from the save file."""
        if not SAVE_FILE.exists():
            logger.info("No save file found at %s", SAVE_FILE)
            return CatData(guild_cats={})

        with SAVE_FILE.open("r") as f:
            try:
                json_data = f.read()
                db = CatData.model_validate_json(json_data)
            except pydantic.ValidationError:
                logger.exception(
                    "Failed to decode CatData from: %s\nCreating new one.", SAVE_FILE
                )
                return CatData(guild_cats={})
            total_cats = 0
            for guild in db.guild_cats:
                total_cats += len(db.guild_cats[guild])
            logger.info(
                "Loaded %d cat(s) for %d guild(s) from %s",
                total_cats,
                len(db.guild_cats),
                SAVE_FILE,
            )
            return db

    def _save_cat_db(self, db: CatData) -> None:
        """Save cats to the save file."""
        if not SAVE_FILE.exists():
            logger.info("No save file found, creating a new one.")
            SAVE_FILE.touch()
        # Save as JSON
        with SAVE_FILE.open("w") as f:
            f.write(db.model_dump_json(indent=4))
        total_cats = 0
        for guild in db.guild_cats:
            total_cats += len(db.guild_cats[guild])
        logger.info(
            "Saved %d cat(s) for %d guild(s) to %s",
            total_cats,
            len(db.guild_cats),
            SAVE_FILE,
        )
