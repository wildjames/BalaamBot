import logging
import pathlib

import pydantic
from rapidfuzz import fuzz, process

import balaambot.config

logger = logging.getLogger(__name__)

SAVE_FILE = pathlib.Path(balaambot.config.PERSISTENT_DATA_DIR) / "cats.json"
MAX_CAT_HUNGER = 100
CAT_FEED_THRESHOLD = 90
CAT_MATCH_THRESHOLD = 50  # percent similarity needed for a fuzzy match


class Cat(pydantic.BaseModel):
    """Data representing a cat."""

    # Cat name, used as the unique identifier
    name: str
    # Discord user ID of the owner
    owner: int
    # Hunger level
    hunger: int = 100


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

        # exact match
        cat_id = self._get_cat_id(cat_name)
        if cat_id in cats:
            return cats[cat_id].name

        # fuzzy match against all IDs
        match = process.extractOne(
            cat_name, cats.keys(), scorer=fuzz.WRatio, score_cutoff=CAT_MATCH_THRESHOLD
        )
        if match:
            matched_id, score, _ = match
            logger.info(
                "Fuzzy matched %r to %r (score=%d)", cat_name, matched_id, score
            )
            return cats[matched_id].name

        return None

    def get_cat_names(self, guild_id: int) -> str:
        """Get a formatted list of cat names, owners, and hunger levels."""
        return "\n".join(
            f"- {cat.name} (Owner: <@{cat.owner}>, Hunger: {cat.hunger})"
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

    def feed_cat(self, cat_name: str, guild_id: int, user_id: int) -> str:
        """Feed a cat to increase its hunger level.

        Args:
            cat_name (str): The name of the cat to feed
            guild_id (int): The Discord guild the cat is in
            user_id (int): The Discord user ID of the user feeding the cat

        Returns:
            str: The result message

        """
        cat_id = self._get_cat_id(cat_name)
        cats = self.db.guild_cats.get(guild_id, {})
        cat_obj = cats.get(cat_id)
        if not cat_obj:
            return f"No cat named {cat_name} exists."
        if cat_obj.owner != user_id:
            return (
                f"You are not the owner of {cat_obj.name}! "
                "Only the owner can feed this cat. :pouting_cat:"
            )

        old_hunger = cat_obj.hunger
        if cat_obj.hunger > CAT_FEED_THRESHOLD:
            return (
                f"{cat_obj.name} doesn't want to eat right now. "
                "They are not hungry enough. :smiley_cat:"
            )
        cat_obj.hunger = MAX_CAT_HUNGER
        self._save_cat_db(self.db)
        return (
            f"<@{user_id}> fed {cat_obj.name}! "
            f"Hunger: {old_hunger} → {cat_obj.hunger} :kissing_cat:"
        )

    def decrease_hunger(self) -> None:
        """Decrease the hunger of all cats by 1."""
        for cats in self.db.guild_cats.values():
            for cat in cats.values():
                # Decrease hunger by 1, but not below 0
                if cat.hunger > 0:
                    cat.hunger -= 1

    def get_hungry_cats(self, threshold: int = 10) -> list[int]:
        """Return a list of user IDs whose cats are hungry (hunger below threshold)."""
        hungry_owners = set()
        for guild_cats in self.db.guild_cats.values():
            for cat in guild_cats.values():
                if cat.hunger < threshold:
                    hungry_owners.add(cat.owner)
        return list(hungry_owners)

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
