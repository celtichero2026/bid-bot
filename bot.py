import os
import json
import traceback
import random
from datetime import datetime, timezone, timedelta

import discord
from discord.ext import commands, tasks
from discord import app_commands

from asyncio import Lock

TOKEN = os.getenv("DISCORD_TOKEN")

LEADER_ROLE_IDS = [
    1415053351116079219,  # main server
    1495844307666731069,  # test server role
]

ALLOWED_CHANNEL_IDS = [
    1447764043090755646,  # Druid
    1447764333894434837,  # Mage
    1447764834132295782,  # Warrior
    1447765010800578782,  # Rogue
    1447765179172524184,  # Ranger
    1447765439366168687,  # No Class Required
    1491844512828489918,  # TEST SERVER
]

OUTBID_INCREMENT = 0.10
DATA_DIR = os.getenv("BIDBOT_DATA_DIR", "./data")
DATA_FILE = os.path.join(DATA_DIR, "bid_state.json")

intents = discord.Intents.default()
intents.message_content = True
intents.messages = True

bot = commands.Bot(command_prefix="!", intents=intents)

bid_state: dict[int, dict] = {}
bid_locks: dict[int, Lock] = {}

roll_state: dict[int, dict] = {}
roll_views_registered = False


def is_leader(
    member: discord.Member | discord.User | None, guild: discord.Guild | None
) -> bool:
    if member is None or guild is None:
        return False
    if not isinstance(member, discord.Member):
        return False
    return any(role.id in LEADER_ROLE_IDS for role in member.roles)

def parse_bid_numbers(value: str) -> list[int]:
    """
    Accepts formats such as:
    1
    1,2,3
    1 2 3
    1-4
    1,3-5,8
    """
    numbers: set[int] = set()

    cleaned = value.replace(" ", ",")

    for part in cleaned.split(","):
        part = part.strip()

        if not part:
            continue

        if "-" in part:
            start_text, end_text = part.split("-", 1)
            start = int(start_text)
            end = int(end_text)

            if start <= 0 or end <= 0 or end < start:
                raise ValueError("Invalid bid-number range.")

            numbers.update(range(start, end + 1))
        else:
            number = int(part)

            if number <= 0:
                raise ValueError("Bid numbers must be greater than zero.")

            numbers.add(number)

    if not numbers:
        raise ValueError("No bid numbers were provided.")

    return sorted(numbers)


@bot.tree.error
async def on_app_command_error(
    interaction: discord.Interaction, error: app_commands.AppCommandError
):
    traceback.print_exception(type(error), error, error.__traceback__)

    if interaction.response.is_done():
        await interaction.followup.send(
            f"Error: {type(error).__name__}: {error}", ephemeral=True
        )
    else:
        await interaction.response.send_message(
            f"Error: {type(error).__name__}: {error}", ephemeral=True
        )


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def get_bid_lock(thread_id: int) -> Lock:
    if thread_id not in bid_locks:
        bid_locks[thread_id] = Lock()
    return bid_locks[thread_id]


def count_user_bids(state: dict, user_id: int) -> int:
    return sum(
        1
        for entry in state.get("bid_log", [])
        if entry.get("valid", False) and entry.get("bidder_id") == user_id
    )


def dt_to_str(dt: datetime) -> str:
    return dt.isoformat()


def str_to_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def ensure_data_dir() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)


def is_allowed_channel(channel) -> bool:
    if channel is None:
        return False
    if channel.id in ALLOWED_CHANNEL_IDS:
        return True
    if isinstance(channel, discord.Thread) and channel.parent_id in ALLOWED_CHANNEL_IDS:
        return True
    return False


def min_outbid_from_min_bid(min_bid: int) -> int:
    return max(10, int(min_bid * OUTBID_INCREMENT))


def phase_label(phase: int) -> str:
    return {
        1: "Phase 1 — Open",
        2: "Phase 2 — Restricted",
        3: "Closed",
    }.get(phase, "Unknown")


def serialize_bid_state() -> dict:
    payload = {}

    for thread_id, state in bid_state.items():
        copy_state = dict(state)
        copy_state["phase1_bidders"] = list(state.get("phase1_bidders", set()))
        copy_state["opted_out_bidders"] = list(state.get("opted_out_bidders", set()))
        payload[str(thread_id)] = copy_state

    return payload


def deserialize_bid_state(raw: dict) -> dict[int, dict]:
    restored = {}

    for thread_id_str, state in raw.items():
        restored[int(thread_id_str)] = {
            **state,
            "phase1_bidders": set(state.get("phase1_bidders", [])),
            "opted_out_bidders": set(state.get("opted_out_bidders", [])),
        }

    return restored


def serialize_roll_state() -> dict:
    return {
        str(roll_id): state
        for roll_id, state in roll_state.items()
    }


def deserialize_roll_state(raw: dict) -> dict[int, dict]:
    restored = {}

    for roll_id_str, state in raw.items():
        restored[int(roll_id_str)] = state

    return restored


def serialize_state() -> dict:
    return {
        "version": 2,
        "bid_state": serialize_bid_state(),
        "roll_state": serialize_roll_state(),
    }


def save_state() -> None:
    ensure_data_dir()

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(serialize_state(), f, indent=2)


def load_state() -> None:
    global bid_state, roll_state

    ensure_data_dir()

    if not os.path.exists(DATA_FILE):
        bid_state = {}
        roll_state = {}
        return

    with open(DATA_FILE, "r", encoding="utf-8") as f:
        raw = json.load(f)

    # New format
    if isinstance(raw, dict) and "bid_state" in raw:
        bid_state = deserialize_bid_state(raw.get("bid_state", {}))
        roll_state = deserialize_roll_state(raw.get("roll_state", {}))
        return

    # Old format fallback — protects your current live bid_state.json
    bid_state = deserialize_bid_state(raw)
    roll_state = {}


def get_state(thread_id: int) -> dict | None:
    return bid_state.get(thread_id)


def get_roll_state(roll_id: int) -> dict | None:
    return roll_state.get(roll_id)


def get_state(thread_id: int) -> dict | None:
    return bid_state.get(thread_id)


def init_state(
    thread_id: int,
    toon: str,
    amount: int,
    min_bid: int,
    bidder_id: int,
    message_id: int | None,
) -> dict:
    now = utcnow()
    outbid_inc = min_outbid_from_min_bid(min_bid)

    state = {
        "phase": 1,
        "phase1_start": dt_to_str(now),
        "last_bid_time": dt_to_str(now),
        "phase1_bidders": {bidder_id},
        "opted_out_bidders": set(),
        "current_bid": amount,
        "current_toon": toon,
        "current_bidder_id": bidder_id,
        "min_bid": min_bid,
        "outbid_inc": outbid_inc,
        "closed": False,
        "phase2_announced": False,
        "closed_announced": False,
        "last_valid_bid": {
            "toon": toon,
            "amount": amount,
            "bidder_id": bidder_id,
            "message_id": message_id,
            "timestamp": dt_to_str(now),
        },
        "bid_log": [
            {
                "bid_number": 1,
                "toon": toon,
                "amount": amount,
                "bidder_id": bidder_id,
                "message_id": message_id,
                "timestamp": dt_to_str(now),
                "valid": True,
                "reason": None,
            }
        ],
    }

    bid_state[thread_id] = state
    return state


def recalc_last_valid_bid(state: dict) -> None:
    for entry in reversed(state["bid_log"]):
        if entry.get("valid"):
            state["last_valid_bid"] = {
                "toon": entry["toon"],
                "amount": entry["amount"],
                "bidder_id": entry["bidder_id"],
                "message_id": entry.get("message_id"),
                "timestamp": entry["timestamp"],
            }
            state["current_toon"] = entry["toon"]
            state["current_bid"] = entry["amount"]
            state["current_bidder_id"] = entry["bidder_id"]
            state["last_bid_time"] = entry["timestamp"]
            return

    state["last_valid_bid"] = None
    state["current_toon"] = ""
    state["current_bid"] = 0
    state["current_bidder_id"] = 0


def recalc_phase1_bidders(state: dict) -> None:
    phase1_start = str_to_dt(state.get("phase1_start"))

    if not phase1_start:
        state["phase1_bidders"] = set()
        return

    cutoff = phase1_start + timedelta(hours=24)

    valid_bidders = set()

    for entry in state.get("bid_log", []):
        if not entry.get("valid"):
            continue

        ts = str_to_dt(entry.get("timestamp"))
        if not ts:
            continue

        if ts <= cutoff:
            valid_bidders.add(entry["bidder_id"])

    state["phase1_bidders"] = valid_bidders

# ──────────────────────────────────────────────────────────────────────────────
# Roll helpers
# ──────────────────────────────────────────────────────────────────────────────


def display_time_left(closes_at_text: str | None) -> str:
    closes_at = str_to_dt(closes_at_text)

    if not closes_at:
        return "Unknown"

    delta = closes_at - utcnow()
    total = max(int(delta.total_seconds()), 0)

    hours, remainder = divmod(total, 3600)
    minutes, _ = divmod(remainder, 60)

    return f"{hours}h {minutes}m"


def get_sorted_rolls(state: dict) -> list[dict]:
    rolls = list(state.get("rolls", {}).values())

    return sorted(
        rolls,
        key=lambda item: (
            -int(item.get("roll", -1)),
            item.get("timestamp", ""),
        ),
    )


def build_roll_panel_content(state: dict, roll_id: int) -> str:
    title = state.get("title", "Roll")
    closed = state.get("closed", False)
    rolls = state.get("rolls", {})
    sorted_rolls = get_sorted_rolls(state)

    status = "Closed" if closed else "Open"
    time_left = "Closed" if closed else display_time_left(state.get("closes_at"))

    lines = [
        f"🎲 **Roll {status} — {title}**",
        f"Roll ID: `{roll_id}`",
        "",
        "Click **Roll** to roll 0–100.",
        "",
        "**Rules:**",
        "• 1 roll per person",
        "• Highest roll wins",
        f"• Time left: **{time_left}**",
        f"• Total rolls: **{len(rolls)}**",
    ]

    if sorted_rolls:
        top = sorted_rolls[0]
        lines.extend(
            [
                "",
                f"Current highest: **{top.get('display_name', 'Unknown')} — {top.get('roll')}**",
            ]
        )

    return "\n".join(lines)


def build_roll_info_content(state: dict, roll_id: int, viewer_id: int | None = None) -> str:
    title = state.get("title", "Roll")
    closed = state.get("closed", False)
    rolls = state.get("rolls", {})
    sorted_rolls = get_sorted_rolls(state)

    status = "Closed" if closed else "Open"
    time_left = "Closed" if closed else display_time_left(state.get("closes_at"))

    lines = [
        f"📊 **Roll Info — {title}**",
        f"Roll ID: `{roll_id}`",
        f"Status: **{status}**",
        f"Time Left: **{time_left}**",
        f"Total Rolls: **{len(rolls)}**",
    ]

    if sorted_rolls:
        highest_value = sorted_rolls[0].get("roll")
        winners = [
            roll
            for roll in sorted_rolls
            if roll.get("roll") == highest_value
        ]

        if len(winners) == 1:
            winner = winners[0]
            lines.extend(
                [
                    "",
                    f"Highest Roll: **{winner.get('display_name', 'Unknown')} — {winner.get('roll')}**",
                ]
            )
        else:
            names = ", ".join(
                winner.get("display_name", "Unknown")
                for winner in winners
            )
            lines.extend(
                [
                    "",
                    f"Highest Roll: **{highest_value}**",
                    f"Tied: **{names}**",
                ]
            )

    if viewer_id is not None:
        viewer_roll = rolls.get(str(viewer_id))

        lines.append("")

        if viewer_roll:
            lines.append(
                f"Your Roll: **{viewer_roll.get('display_name', 'You')} — {viewer_roll.get('roll')}**"
            )
        else:
            lines.append("Your Roll: **Not rolled yet**")

    if sorted_rolls:
        lines.append("")
        lines.append("**Top Rolls:**")

        for index, roll in enumerate(sorted_rolls[:20], start=1):
            lines.append(
                f"{index}. **{roll.get('display_name', 'Unknown')}** — {roll.get('roll')}"
            )

        if len(sorted_rolls) > 20:
            lines.append(f"\nShowing top 20 of {len(sorted_rolls)} rolls.")

    return "\n".join(lines)


def build_roll_closed_content(state: dict, roll_id: int) -> str:
    title = state.get("title", "Roll")
    sorted_rolls = get_sorted_rolls(state)

    lines = [
        f"🏁 **Roll Closed — {title}**",
        f"Roll ID: `{roll_id}`",
    ]

    if not sorted_rolls:
        lines.append("")
        lines.append("No rolls recorded.")
        return "\n".join(lines)

    highest_value = sorted_rolls[0].get("roll")
    winners = [
        roll
        for roll in sorted_rolls
        if roll.get("roll") == highest_value
    ]

    lines.append("")

    if len(winners) == 1:
        winner = winners[0]
        lines.append(
            f"Winner: **{winner.get('display_name', 'Unknown')} — {winner.get('roll')}**"
        )
    else:
        names = ", ".join(
            winner.get("display_name", "Unknown")
            for winner in winners
        )
        lines.append(f"Tie: **{names}** — **{highest_value}**")

    lines.append("")
    lines.append("**Top Rolls:**")

    for index, roll in enumerate(sorted_rolls[:10], start=1):
        lines.append(
            f"{index}. **{roll.get('display_name', 'Unknown')}** — {roll.get('roll')}"
        )

    if len(sorted_rolls) > 10:
        lines.append(f"\nShowing top 10 of {len(sorted_rolls)} rolls.")

    return "\n".join(lines)


async def close_roll_window(roll_id: int, announce: bool = True) -> tuple[bool, str]:
    state = get_roll_state(roll_id)

    if state is None:
        return False, "Roll window not found."

    if state.get("closed"):
        return False, "Roll window is already closed."

    state["closed"] = True
    state["closed_at"] = dt_to_str(utcnow())
    save_state()

    channel_id = state.get("channel_id")
    channel = bot.get_channel(channel_id)

    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return True, "Roll closed, but I could not fetch the channel."

    try:
        message = await channel.fetch_message(roll_id)
        await message.edit(
            content=build_roll_panel_content(state, roll_id),
            view=None,
        )
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        pass

    if announce:
        try:
            await channel.send(
                build_roll_closed_content(state, roll_id),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.Forbidden, discord.HTTPException):
            pass

    return True, "Roll window closed."


async def handle_roll_button(interaction: discord.Interaction):
    if interaction.message is None:
        await interaction.response.send_message(
            "Roll panel not found.",
            ephemeral=True,
        )
        return

    roll_id = interaction.message.id
    state = get_roll_state(roll_id)

    if state is None:
        await interaction.response.send_message(
            "This roll window was not found.",
            ephemeral=True,
        )
        return

    closes_at = str_to_dt(state.get("closes_at"))

    if state.get("closed") or (closes_at and utcnow() >= closes_at):
        if not state.get("closed"):
            await close_roll_window(roll_id, announce=True)

        await interaction.response.send_message(
            "This roll window is closed.",
            ephemeral=True,
        )
        return

    user_id = str(interaction.user.id)
    rolls = state.setdefault("rolls", {})

    if user_id in rolls:
        existing = rolls[user_id]

        await interaction.response.send_message(
            f"❌ You already rolled for **{state.get('title', 'this roll')}**.\n"
            f"Your roll: **{existing.get('roll')}**",
            ephemeral=True,
        )
        return

    display_name = (
        getattr(interaction.user, "display_name", None)
        or getattr(interaction.user, "name", "Unknown")
    )

    roll_value = random.randint(0, 100)

    rolls[user_id] = {
        "user_id": interaction.user.id,
        "display_name": display_name,
        "roll": roll_value,
        "timestamp": dt_to_str(utcnow()),
    }

    save_state()

    await interaction.response.send_message(
        f"🎲 **{display_name}** rolled **{roll_value}** for **{state.get('title', 'Roll')}**.",
        allowed_mentions=discord.AllowedMentions.none(),
    )

    try:
        await interaction.message.edit(
            content=build_roll_panel_content(state, roll_id),
            view=RollView(),
        )
    except (discord.Forbidden, discord.HTTPException):
        pass


async def handle_roll_info_button(interaction: discord.Interaction):
    if interaction.message is None:
        await interaction.response.send_message(
            "Roll panel not found.",
            ephemeral=True,
        )
        return

    roll_id = interaction.message.id
    state = get_roll_state(roll_id)

    if state is None:
        await interaction.response.send_message(
            "This roll window was not found.",
            ephemeral=True,
        )
        return

    closes_at = str_to_dt(state.get("closes_at"))

    if not state.get("closed") and closes_at and utcnow() >= closes_at:
        await close_roll_window(roll_id, announce=True)

    await interaction.response.send_message(
        build_roll_info_content(
            state,
            roll_id,
            viewer_id=interaction.user.id,
        ),
        ephemeral=True,
    )


class RollView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Roll",
        emoji="🎲",
        style=discord.ButtonStyle.primary,
        custom_id="bidbot_roll_button",
    )
    async def roll_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        await handle_roll_button(interaction)

    @discord.ui.button(
        label="Roll Info",
        emoji="📊",
        style=discord.ButtonStyle.secondary,
        custom_id="bidbot_roll_info_button",
    )
    async def roll_info_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        await handle_roll_info_button(interaction)
        
# ──────────────────────────────────────────────────────────────────────────────
# Bot lifecycle
# ──────────────────────────────────────────────────────────────────────────────


@bot.event
async def on_ready():
    global roll_views_registered

    load_state()

    if not roll_views_registered:
        bot.add_view(RollView())
        roll_views_registered = True

    await bot.tree.sync()

    if not phase_checker.is_running():
        phase_checker.start()

    if not roll_checker.is_running():
        roll_checker.start()

    print(f"Logged in as {bot.user}")
    print("Phase checker running:", phase_checker.is_running())
    print("Roll checker running:", roll_checker.is_running())
    print("Loaded bid states:", len(bid_state))
    print("Loaded roll states:", len(roll_state))
    


# ──────────────────────────────────────────────────────────────────────────────
# Thread chat discouragement
# ──────────────────────────────────────────────────────────────────────────────


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        await bot.process_commands(message)
        return

    channel = message.channel

    # Only monitor threads
    if not isinstance(channel, discord.Thread):
        await bot.process_commands(message)
        return

    # Only monitor allowed bid channels
    if not is_allowed_channel(channel):
        await bot.process_commands(message)
        return

    # Only monitor active bid threads
    state = get_state(channel.id)
    if state is None:
        await bot.process_commands(message)
        return

    content = (message.content or "").strip().lower()

    # User opts out of mentions
    if content == "out":
        state.setdefault("opted_out_bidders", set()).add(message.author.id)
        save_state()

        await channel.send(
            f"🚪 {message.author.mention} is out and will not be mentioned in future bid updates.",
            allowed_mentions=discord.AllowedMentions(users=True),
        )
        return

    # Allow leader comments
    if is_leader(message.author, message.guild):
        await bot.process_commands(message)
        return

    # Allow approved utility commands
    ALLOWED_THREAD_PREFIXES = (
        "%pay",
        "%undo",
        "%refund",
    )

    if any(content.startswith(prefix) for prefix in ALLOWED_THREAD_PREFIXES):
        await bot.process_commands(message)
        return

    # Allow slash command messages
    if content.startswith("/"):
        await bot.process_commands(message)
        return

    # React to chatter
    try:
        await message.add_reaction("❌")
    except (discord.Forbidden, discord.HTTPException):
        pass

    # Warning message
    try:
        await channel.send(
            f"{message.author.mention} Please keep this thread clean. "
            "Use `/bid` to bid, `/review` for concerns, `out` to stop future mentions, or approved mod payout commands.",
            delete_after=12,
            allowed_mentions=discord.AllowedMentions(users=True),
        )
    except (discord.Forbidden, discord.HTTPException):
        pass

    await bot.process_commands(message)


# ──────────────────────────────────────────────────────────────────────────────
# Background phase watcher
# ──────────────────────────────────────────────────────────────────────────────


@tasks.loop(minutes=1)
async def phase_checker():
    now = utcnow()
    dirty = False

    for thread_id, state in list(bid_state.items()):

        print(f"[PHASE CHECK] Thread {thread_id} | Phase: {state['phase']}")

        # Skip already closed bids
        if state.get("closed") or state.get("phase") == 3:
            continue

        thread = bot.get_channel(thread_id)

        if thread is None:
            try:
                thread = await bot.fetch_channel(thread_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                continue

        phase1_start = str_to_dt(state.get("phase1_start"))
        last_bid_time = str_to_dt(state.get("last_bid_time"))

        if phase1_start is None or last_bid_time is None:
            continue

        # ─────────────────────────────────────────────
        # Move to Phase 2 after 24h
        # ─────────────────────────────────────────────
        if state["phase"] == 1 and now >= phase1_start + timedelta(hours=24):

            state["phase"] = 2
            dirty = True

            if not state.get("phase2_announced", False):

                bidders = state.get("phase1_bidders", set())
                opted_out = state.get("opted_out_bidders", set())

                mentions = " ".join(
                    f"<@{uid}>"
                    for uid in bidders
                    if uid not in opted_out
                )

                if mentions:
                    await thread.send(
                        "⏰ **Phase 2 — Restricted Bidding**\n"
                        "Only users who placed a valid bid in the first 24 hours can continue bidding.\n"
                        f"{mentions}",
                        allowed_mentions=discord.AllowedMentions(users=True),
                    )
                else:
                    await thread.send(
                        "⏰ **Phase 2 — Restricted Bidding**\n"
                        "No eligible phase 1 bidders remain."
                    )

                state["phase2_announced"] = True
                dirty = True

        # ─────────────────────────────────────────────
        # Close bidding 12h after last valid phase 2 bid
        # ─────────────────────────────────────────────
        if state["phase"] == 2 and now >= last_bid_time + timedelta(hours=12):

            state["phase"] = 3
            state["closed"] = True
            dirty = True

            if not state.get("closed_announced", False):

                last_valid = state.get("last_valid_bid")

                if last_valid:
                    toon = last_valid["toon"]
                    amount = last_valid["amount"]

                    await thread.send(
                        "🔒 **Bidding Closed**\n"
                        f"Final bid: **{toon}** — **{amount:,}**\n"
                        f"Cash out with: `%pay {toon} {amount}`"
                    )
                else:
                    await thread.send(
                        "🔒 **Bidding Closed** — No valid bids recorded."
                    )

                state["closed_announced"] = True
                dirty = True

    if dirty:
        save_state()

@phase_checker.before_loop
async def before_phase_checker():
    await bot.wait_until_ready()


# ──────────────────────────────────────────────────────────────────────────────
# Background roll watcher
# ──────────────────────────────────────────────────────────────────────────────


@tasks.loop(minutes=1)
async def roll_checker():
    now = utcnow()

    for roll_id, state in list(roll_state.items()):
        if state.get("closed"):
            continue

        closes_at = str_to_dt(state.get("closes_at"))

        if closes_at and now >= closes_at:
            await close_roll_window(roll_id, announce=True)


@roll_checker.before_loop
async def before_roll_checker():
    await bot.wait_until_ready()

# ──────────────────────────────────────────────────────────────────────────────
# Commands
# ──────────────────────────────────────────────────────────────────────────────

@bot.tree.command(name="roll", description="Open a roll panel")
@app_commands.describe(
    title="What this roll is for",
    duration_hours="How long the roll stays open. Default is 24 hours.",
)
async def roll(
    interaction: discord.Interaction,
    title: str,
    duration_hours: int = 24,
):
    if not is_allowed_channel(interaction.channel):
        await interaction.response.send_message(
            "Use this in bid channels only.",
            ephemeral=True,
        )
        return

    if interaction.guild is None or not is_leader(interaction.user, interaction.guild):
        await interaction.response.send_message(
            "Only leaders can open roll panels.",
            ephemeral=True,
        )
        return

    channel = interaction.channel

    if channel is None:
        await interaction.response.send_message(
            "Channel not found.",
            ephemeral=True,
        )
        return

    title = title.strip()

    if not title:
        await interaction.response.send_message(
            "Roll title cannot be blank.",
            ephemeral=True,
        )
        return

    if duration_hours <= 0:
        await interaction.response.send_message(
            "Duration must be at least 1 hour.",
            ephemeral=True,
        )
        return

    await interaction.response.send_message(
        f"🎲 **Roll Open — {title}**\nSetting up roll panel...",
        view=RollView(),
        allowed_mentions=discord.AllowedMentions.none(),
    )

    sent = await interaction.original_response()

    now = utcnow()

    roll_state[sent.id] = {
        "title": title,
        "channel_id": channel.id,
        "message_id": sent.id,
        "created_by": interaction.user.id,
        "created_at": dt_to_str(now),
        "closes_at": dt_to_str(now + timedelta(hours=duration_hours)),
        "closed": False,
        "closed_at": None,
        "rolls": {},
    }

    save_state()

    await sent.edit(
        content=build_roll_panel_content(roll_state[sent.id], sent.id),
        view=RollView(),
    )

@bot.tree.command(name="closeroll", description="Close a roll panel early")
@app_commands.describe(
    roll_id="The Roll ID shown on the roll panel",
)
async def closeroll(interaction: discord.Interaction, roll_id: str):
    if interaction.guild is None or not is_leader(interaction.user, interaction.guild):
        await interaction.response.send_message(
            "Only leaders can close roll panels.",
            ephemeral=True,
        )
        return

    try:
        parsed_roll_id = int(roll_id.strip())
    except ValueError:
        await interaction.response.send_message(
            "Roll ID must be the number shown on the roll panel.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True)

    success, message = await close_roll_window(
        parsed_roll_id,
        announce=True,
    )

    await interaction.followup.send(
        message,
        ephemeral=True,
    )

@bot.tree.command(name="ping")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("pong")


@bot.tree.command(name="open", description="Open a new bid thread")
@app_commands.describe(
    toon="The toon name for the opening bid",
    amount="Opening bid amount",
    min_bid="Minimum bid amount for this item",
)
async def open_bid(
    interaction: discord.Interaction, toon: str, amount: int, min_bid: int
):
    channel = interaction.channel

    if not is_allowed_channel(channel):
        await interaction.response.send_message(
            "Use this in bid channels only.", ephemeral=True
        )
        return

    if channel is None:
        await interaction.response.send_message("Channel not found.", ephemeral=True)
        return

    if get_state(channel.id) is not None:
        await interaction.response.send_message(
            "A bid is already open in this thread. Use `/bid` to outbid.",
            ephemeral=True,
        )
        return

    if min_bid < 0 or amount < 0:
        await interaction.response.send_message(
            "Amounts cannot be negative.",
            ephemeral=True,
        )
        return

    if amount < min_bid:
        await interaction.response.send_message(
            f"Opening bid **{amount:,}** is below the minimum bid **{min_bid:,}**.",
            ephemeral=True,
        )
        return

    outbid_inc = min_outbid_from_min_bid(min_bid)

    await interaction.response.send_message(
        f"✅ Bid opened\n"
        f"{toon} {amount:,} | Min bid: {min_bid:,} | Min outbid: {outbid_inc:,}",
        allowed_mentions=discord.AllowedMentions.none(),
    )

    sent = await interaction.original_response()

    init_state(
        thread_id=channel.id,
        toon=toon,
        amount=amount,
        min_bid=min_bid,
        bidder_id=interaction.user.id,
        message_id=sent.id,
    )
    save_state()


@bot.tree.command(name="bid", description="Place an outbid")
@app_commands.describe(
    toon="The toon name you are bidding on", amount="Your bid amount"
)
async def bid(interaction: discord.Interaction, toon: str, amount: int):
    channel = interaction.channel

    async def reply(message: str, ephemeral: bool = True):
        if not interaction.response.is_done():
            await interaction.response.send_message(message, ephemeral=ephemeral)
        else:
            await interaction.followup.send(message, ephemeral=ephemeral)

    if not isinstance(channel, discord.Thread):
        await reply("Bids must be placed inside a bid thread.")
        return

    if not is_allowed_channel(channel):
        await reply("This is not a valid bidding channel.")
        return

    state = get_state(channel.id)
    if state is None:
        await reply("No bid is open in this thread. Use `/open` first.")
        return

    lock = get_bid_lock(channel.id)

    async with lock:
        if state["phase"] == 3 or state["closed"]:
            await reply("🔒 Bidding is closed for this item.")
            return

        phase1_bidders = state.get("phase1_bidders", set())
        if (
            state["phase"] == 2
            and phase1_bidders
            and interaction.user.id not in phase1_bidders
        ):
            await reply(
                "⏰ Bidding is in Phase 2 and restricted to users who placed a valid bid in the first 24 hours."
            )
            return

        user_bid_count = count_user_bids(state, interaction.user.id)

        if user_bid_count >= 7:
            await reply(
                f"❌ You have reached the maximum of 7 bids for this item. ({user_bid_count}/7)"
            )
            return

        if amount < 0:
            await reply("Bid cannot be negative.")
            return

        current = state["current_bid"]
        min_bid = state["min_bid"]
        min_outbid = state["outbid_inc"]

        if current is None:
            if amount < min_bid:
                await reply(f"Opening bid must be at least {min_bid:,}.")
                return
        else:
            required = current + min_outbid
            if amount < required:
                await reply(f"You must bid at least {required:,}.")
                return

        now_str = datetime.now(timezone.utc).isoformat()
        bid_number = len(state.get("bid_log", [])) + 1

        state["current_bid"] = amount
        state["current_toon"] = toon
        state["current_bidder_id"] = interaction.user.id
        state["last_bid_time"] = now_str

        phase1_start = str_to_dt(state.get("phase1_start"))
        if phase1_start and datetime.now(timezone.utc) <= phase1_start + timedelta(
            hours=24
        ):
            state["phase1_bidders"].add(interaction.user.id)

        state["last_valid_bid"] = {
            "bid_number": bid_number,
            "toon": toon,
            "amount": amount,
            "bidder_id": interaction.user.id,
            "message_id": None,
            "timestamp": now_str,
        }

        state.setdefault("bid_log", []).append(
            {
                "bid_number": bid_number,
                "toon": toon,
                "amount": amount,
                "bidder_id": interaction.user.id,
                "message_id": None,
                "timestamp": now_str,
                "valid": True,
                "reason": None,
            }
        )

        remaining = 7 - (user_bid_count + 1)

        participants = state.get("phase1_bidders", set())
        opted_out = state.get("opted_out_bidders", set())
        mentions = " ".join(f"<@{uid}>" for uid in participants if uid not in opted_out)

        if not interaction.response.is_done():
            await interaction.response.send_message(
                f"💰 **New Bid #{bid_number}!**\n"
                f"__**{toon}**__ → {amount:,}\n"
                f"Next min: {amount + min_outbid:,}\n"
                f"Bids remaining: {remaining}\n"
                f"{mentions}",
                allowed_mentions=discord.AllowedMentions(users=True),
            )
        else:
            await interaction.followup.send(
                f"💰 **New Bid #{bid_number}!**\n"
                f"__**{toon}**__ → {amount:,}\n"
                f"Next min: {amount + min_outbid:,}\n"
                f"Bids remaining: {remaining}\n"
                f"{mentions}",
                allowed_mentions=discord.AllowedMentions(users=True),
            )

        sent = await interaction.original_response()

        state["last_valid_bid"]["message_id"] = sent.id
        state["bid_log"][-1]["message_id"] = sent.id

        save_state()


@bot.tree.command(name="history", description="Show bid history for this thread")
async def history(interaction: discord.Interaction):
    if not is_allowed_channel(interaction.channel):
        await interaction.response.send_message(
            "Use this in bid channels only.", ephemeral=True
        )
        return

    channel = interaction.channel
    if channel is None:
        await interaction.response.send_message("Channel not found.", ephemeral=True)
        return

    state = get_state(channel.id)
    if state is None:
        await interaction.response.send_message(
            "No bid is open in this thread.", ephemeral=True
        )
        return

    bid_log = state.get("bid_log", [])

    if not bid_log:
        await interaction.response.send_message("No bids recorded yet.", ephemeral=True)
        return

    lines = ["📜 **Bid History**"]

    for entry in bid_log[-20:]:
        bid_number = entry.get("bid_number", "?")
        toon = entry.get("toon", "Unknown")
        amount = entry.get("amount", 0)
        valid = entry.get("valid", False)

        status = "✅"
        extra = ""

        if not valid:
            status = "❌"
            reason = entry.get("reason")
            if reason:
                extra = f" — {reason}"

        elif entry.get("corrected"):
            status = "✏️"
            old_amount = entry.get("old_amount")
            reason = entry.get("correction_reason")
            if old_amount:
                extra = f" — was {old_amount:,}"
            if reason:
                extra += f" — {reason}"

        lines.append(f"{status} **#{bid_number}** — **{toon}**: **{amount:,}**{extra}")

    if len(bid_log) > 20:
        lines.append(f"\nShowing last 20 of {len(bid_log)} bids.")

    await interaction.response.send_message("\n".join(lines), ephemeral=True)


@bot.tree.command(name="review", description="Flag a concern for leaders")
@app_commands.describe(reason="Briefly describe the issue")
async def review(interaction: discord.Interaction, reason: str):
    if not is_allowed_channel(interaction.channel):
        await interaction.response.send_message(
            "Use this in bid channels only.", ephemeral=True
        )
        return

    if interaction.guild is None:
        await interaction.response.send_message("Guild not found.", ephemeral=True)
        return

    mentions = [f"<@&{role_id}>" for role_id in LEADER_ROLE_IDS]

    await interaction.response.send_message(
        f"{' '.join(mentions)} Review requested by {interaction.user.mention}: {reason}",
        allowed_mentions=discord.AllowedMentions(roles=True, users=True),
    )


@bot.tree.command(name="bidinfo", description="Show current bid info for this thread")
async def bidinfo(interaction: discord.Interaction):
    if not is_allowed_channel(interaction.channel):
        await interaction.response.send_message(
            "Use this in bid channels only.", ephemeral=True
        )
        return

    channel = interaction.channel
    if channel is None:
        await interaction.response.send_message("Channel not found.", ephemeral=True)
        return

    state = get_state(channel.id)
    if state is None:
        await interaction.response.send_message(
            "No bid is open in this thread.", ephemeral=True
        )
        return

    now = utcnow()
    phase1_start = str_to_dt(state["phase1_start"])
    last_bid_time = str_to_dt(state["last_bid_time"])

    phase2_eta = "N/A"
    close_eta = "N/A"

    if phase1_start and state["phase"] == 1:
        delta = (phase1_start + timedelta(hours=24)) - now
        total = max(int(delta.total_seconds()), 0)
        h, m = divmod(total // 60, 60)
        phase2_eta = f"{h}h {m}m"

    if last_bid_time and state["phase"] == 2:
        delta = (last_bid_time + timedelta(hours=12)) - now
        total = max(int(delta.total_seconds()), 0)
        h, m = divmod(total // 60, 60)
        close_eta = f"{h}h {m}m"

    next_valid = state["current_bid"] + state["outbid_inc"]
    bidder_count = len(state.get("phase1_bidders", set()))

    await interaction.response.send_message(
        f"📊 **Bid Status**\n"
        f"Toon: **{state['current_toon']}**\n"
        f"Current Bid: **{state['current_bid']:,}**\n"
        f"Min Bid: **{state['min_bid']:,}**\n"
        f"Min Outbid: **{state['outbid_inc']:,}**\n"
        f"Next Valid Bid: **{next_valid:,}**\n"
        f"Phase: **{phase_label(state['phase'])}**\n"
        f"Eligible Phase 2 Bidders: **{bidder_count}**\n"
        f"Phase 2 Starts In: **{phase2_eta}**\n"
        f"Close In: **{close_eta}**",
        ephemeral=True,
    )


@bot.tree.command(
    name="setminbid", description="Change the minimum bid for this thread"
)
@app_commands.describe(min_bid="Corrected minimum bid")
async def setminbid(interaction: discord.Interaction, min_bid: int):
    if not is_allowed_channel(interaction.channel):
        await interaction.response.send_message(
            "Use this in bid channels only.", ephemeral=True
        )
        return

    if interaction.guild is None or not is_leader(interaction.user, interaction.guild):
        await interaction.response.send_message(
            "Only leaders can adjust the minimum bid.", ephemeral=True
        )
        return

    channel = interaction.channel
    if channel is None:
        await interaction.response.send_message("Channel not found.", ephemeral=True)
        return

    state = get_state(channel.id)
    if state is None:
        await interaction.response.send_message(
            "No open auction found in this thread.", ephemeral=True
        )
        return

    if min_bid < 0:
        await interaction.response.send_message(
            "Minimum bid cannot be negative.", ephemeral=True
        )
        return

    state["min_bid"] = min_bid
    state["outbid_inc"] = min_outbid_from_min_bid(min_bid)
    save_state()

    await interaction.response.send_message(
        f"✏️ Min bid updated to **{min_bid:,}**. "
        f"Min outbid is now **{state['outbid_inc']:,}**."
    )

@bot.tree.command(name="all_in", description="Bid all remaining EKP even if below normal min outbid")
@app_commands.describe(
    toon="The toon name you are bidding on",
    amount="Your all-in bid amount"
)
async def all_in(interaction: discord.Interaction, toon: str, amount: int):
    channel = interaction.channel

    async def reply(message: str, ephemeral: bool = True):
        if not interaction.response.is_done():
            await interaction.response.send_message(message, ephemeral=ephemeral)
        else:
            await interaction.followup.send(message, ephemeral=ephemeral)

    if not isinstance(channel, discord.Thread):
        await reply("All-in bids must be placed inside a bid thread.")
        return

    if not is_allowed_channel(channel):
        await reply("This is not a valid bidding channel.")
        return

    state = get_state(channel.id)
    if state is None:
        await reply("No bid is open in this thread. Use `/open` first.")
        return

    lock = get_bid_lock(channel.id)

    async with lock:
        if state["phase"] == 3 or state["closed"]:
            await reply("🔒 Bidding is closed for this item.")
            return

        phase1_bidders = state.get("phase1_bidders", set())
        if state["phase"] == 2 and phase1_bidders and interaction.user.id not in phase1_bidders:
            await reply("⏰ Phase 2 is restricted to users who bid in the first 24 hours.")
            return

        user_bid_count = count_user_bids(state, interaction.user.id)
        if user_bid_count >= 7:
            await reply("❌ You have reached the maximum of 7 bids for this item.")
            return

        if amount < 0:
            await reply("Bid cannot be negative.")
            return

        current = state["current_bid"]

        if amount <= current:
            await reply(f"All-in bid must still be higher than the current bid of **{current:,}**.")
            return

        now_str = utcnow().isoformat()
        bid_number = len(state.get("bid_log", [])) + 1

        state["current_bid"] = amount
        state["current_toon"] = toon
        state["current_bidder_id"] = interaction.user.id
        state["last_bid_time"] = now_str

        phase1_start = str_to_dt(state.get("phase1_start"))
        if phase1_start and utcnow() <= phase1_start + timedelta(hours=24):
            state["phase1_bidders"].add(interaction.user.id)

        entry = {
            "bid_number": bid_number,
            "toon": toon,
            "amount": amount,
            "bidder_id": interaction.user.id,
            "message_id": None,
            "timestamp": now_str,
            "valid": True,
            "reason": "ALL IN",
            "all_in": True,
        }

        state.setdefault("bid_log", []).append(entry)
        state["last_valid_bid"] = entry

        remaining = 7 - (user_bid_count + 1)

        participants = state.get("phase1_bidders", set())
        opted_out = state.get("opted_out_bidders", set())
        mentions = " ".join(f"<@{uid}>" for uid in participants if uid not in opted_out)

        await interaction.response.send_message(
            f"🔥 **ALL IN Bid #{bid_number}!**\n"
            f"__**{toon}**__ → {amount:,}\n"
            f"Bids remaining: {remaining}\n"
            f"{mentions}",
            allowed_mentions=discord.AllowedMentions(users=True),
        )

        sent = await interaction.original_response()
        state["bid_log"][-1]["message_id"] = sent.id
        state["last_valid_bid"]["message_id"] = sent.id

        save_state()

@bot.tree.command(
    name="correctbid",
    description="Correct the amount, toon name, or both on a bid",
)
@app_commands.describe(
    bid_number="The bid number to correct",
    reason="Why the bid is being corrected",
    amount="Corrected amount, if needed",
    toon="Corrected toon name, if needed",
)
async def correctbid(
    interaction: discord.Interaction,
    bid_number: int,
    reason: str,
    amount: int | None = None,
    toon: str | None = None,
):
    if not is_allowed_channel(interaction.channel):
        await interaction.response.send_message(
            "Use this in bid channels only.",
            ephemeral=True,
        )
        return

    if interaction.guild is None or not is_leader(
        interaction.user,
        interaction.guild,
    ):
        await interaction.response.send_message(
            "Only leaders can correct bids.",
            ephemeral=True,
        )
        return

    channel = interaction.channel
    if channel is None:
        await interaction.response.send_message(
            "Channel not found.",
            ephemeral=True,
        )
        return

    state = get_state(channel.id)
    if state is None:
        await interaction.response.send_message(
            "No auction found in this thread.",
            ephemeral=True,
        )
        return

    if amount is None and toon is None:
        await interaction.response.send_message(
            "Enter a corrected `amount`, `toon`, or both.",
            ephemeral=True,
        )
        return

    if amount is not None and amount < 0:
        await interaction.response.send_message(
            "Corrected amount cannot be negative.",
            ephemeral=True,
        )
        return

    if toon is not None:
        toon = toon.strip()

        if not toon:
            await interaction.response.send_message(
                "Corrected toon name cannot be blank.",
                ephemeral=True,
            )
            return

    lock = get_bid_lock(channel.id)

    async with lock:
        bid_log = state.get("bid_log", [])
        target_index = None

        for index, entry in enumerate(bid_log):
            if entry.get("bid_number") == bid_number:
                target_index = index
                break

        if target_index is None:
            await interaction.response.send_message(
                f"No bid found with bid number **#{bid_number}**.",
                ephemeral=True,
            )
            return

        target = bid_log[target_index]

        if not target.get("valid"):
            await interaction.response.send_message(
                f"Bid **#{bid_number}** is invalid and cannot be corrected.",
                ephemeral=True,
            )
            return

        old_amount = target["amount"]
        old_toon = target["toon"]

        amount_changed = amount is not None and amount != old_amount
        toon_changed = toon is not None and toon != old_toon

        if not amount_changed and not toon_changed:
            await interaction.response.send_message(
                "The corrected values are the same as the current bid.",
                ephemeral=True,
            )
            return

        if amount_changed:
            previous_valid = None
            next_valid = None

            for entry in reversed(bid_log[:target_index]):
                if entry.get("valid"):
                    previous_valid = entry
                    break

            for entry in bid_log[target_index + 1:]:
                if entry.get("valid"):
                    next_valid = entry
                    break

            min_outbid = state["outbid_inc"]

            if previous_valid:
                minimum_allowed = previous_valid["amount"] + min_outbid
            else:
                minimum_allowed = state["min_bid"]

            if amount < minimum_allowed:
                await interaction.response.send_message(
                    f"Corrected amount must be at least "
                    f"**{minimum_allowed:,}**.",
                    ephemeral=True,
                )
                return

            if next_valid:
                maximum_allowed = next_valid["amount"] - min_outbid

                if amount > maximum_allowed:
                    await interaction.response.send_message(
                        f"Corrected amount cannot exceed "
                        f"**{maximum_allowed:,}**, because the next valid bid "
                        f"is **#{next_valid.get('bid_number')} — "
                        f"{next_valid['amount']:,}**.",
                        ephemeral=True,
                    )
                    return

        corrections = target.setdefault("corrections", [])

        correction_record = {
            "reason": reason,
            "corrected_by": interaction.user.id,
            "timestamp": dt_to_str(utcnow()),
        }

        changes: list[str] = []

        if amount_changed:
            correction_record["old_amount"] = old_amount
            correction_record["new_amount"] = amount

            target["old_amount"] = old_amount
            target["amount"] = amount

            changes.append(
                f"Amount: **{old_amount:,} → {amount:,}**"
            )

        if toon_changed:
            correction_record["old_toon"] = old_toon
            correction_record["new_toon"] = toon

            target["old_toon"] = old_toon
            target["toon"] = toon

            changes.append(
                f"Toon: **{old_toon} → {toon}**"
            )

        corrections.append(correction_record)

        target["corrected"] = True
        target["correction_reason"] = reason

        recalc_last_valid_bid(state)
        recalc_phase1_bidders(state)
        save_state()

        target_message_id = target.get("message_id")

        if target_message_id:
            try:
                message = await channel.fetch_message(target_message_id)
                await message.add_reaction("✏️")
            except (
                discord.NotFound,
                discord.Forbidden,
                discord.HTTPException,
            ):
                pass

        new_last = state.get("last_valid_bid")

        response_lines = [
            f"✏️ Bid **#{bid_number}** corrected.",
            *changes,
            f"Reason: {reason}",
        ]

        if new_last:
            response_lines.append(
                f"Current valid bid: "
                f"**{new_last['toon']} — {new_last['amount']:,}**"
            )

        await interaction.response.send_message(
            "\n".join(response_lines)
        )


@bot.tree.command(
    name="invalidate",
    description="Invalidate one or more bids by bid number",
)
@app_commands.describe(
    bid_numbers="Bid numbers, such as 2,4,7 or 2-5",
    reason="Why these bids are being invalidated",
)
async def invalidate(
    interaction: discord.Interaction,
    bid_numbers: str,
    reason: str,
):
    if not is_allowed_channel(interaction.channel):
        await interaction.response.send_message(
            "Use this in bid channels only.",
            ephemeral=True,
        )
        return

    if interaction.guild is None or not is_leader(
        interaction.user,
        interaction.guild,
    ):
        await interaction.response.send_message(
            "Only leaders can invalidate bids.",
            ephemeral=True,
        )
        return

    channel = interaction.channel
    if channel is None:
        await interaction.response.send_message(
            "Channel not found.",
            ephemeral=True,
        )
        return

    state = get_state(channel.id)
    if state is None:
        await interaction.response.send_message(
            "No auction found in this thread.",
            ephemeral=True,
        )
        return

    try:
        requested_numbers = parse_bid_numbers(bid_numbers)
    except ValueError:
        await interaction.response.send_message(
            "Enter bid numbers like `2`, `2,4,7`, or `2-5`.",
            ephemeral=True,
        )
        return

    lock = get_bid_lock(channel.id)

    async with lock:
        entries_by_number = {
            entry.get("bid_number"): entry
            for entry in state.get("bid_log", [])
        }

        invalidated: list[int] = []
        already_invalid: list[int] = []
        not_found: list[int] = []
        message_ids: list[int] = []

        for bid_number in requested_numbers:
            target = entries_by_number.get(bid_number)

            if target is None:
                not_found.append(bid_number)
                continue

            if not target.get("valid"):
                already_invalid.append(bid_number)
                continue

            target["valid"] = False
            target["reason"] = f"Invalidated by leader: {reason}"
            invalidated.append(bid_number)

            if target.get("message_id"):
                message_ids.append(target["message_id"])

        if not invalidated:
            details = []

            if already_invalid:
                details.append(
                    "Already invalid: "
                    + ", ".join(f"#{number}" for number in already_invalid)
                )

            if not_found:
                details.append(
                    "Not found: "
                    + ", ".join(f"#{number}" for number in not_found)
                )

            await interaction.response.send_message(
                "No bids were invalidated.\n" + "\n".join(details),
                ephemeral=True,
            )
            return

        recalc_last_valid_bid(state)
        recalc_phase1_bidders(state)
        save_state()

        for message_id in message_ids:
            try:
                message = await channel.fetch_message(message_id)
                await message.add_reaction("❌")
            except (
                discord.NotFound,
                discord.Forbidden,
                discord.HTTPException,
            ):
                pass

        lines = [
            "❌ **Bids Invalidated**",
            "Bids: " + ", ".join(f"**#{number}**" for number in invalidated),
            f"Reason: {reason}",
        ]

        if already_invalid:
            lines.append(
                "Already invalid: "
                + ", ".join(f"#{number}" for number in already_invalid)
            )

        if not_found:
            lines.append(
                "Not found: "
                + ", ".join(f"#{number}" for number in not_found)
            )

        new_last = state.get("last_valid_bid")

        if new_last:
            lines.append(
                f"Current valid bid: "
                f"**{new_last['toon']} — {new_last['amount']:,}**"
            )
        else:
            lines.append("There are no remaining valid bids.")

        await interaction.response.send_message("\n".join(lines))


@bot.tree.command(name="closebid", description="Force close the current bid thread")
async def closebid(interaction: discord.Interaction):
    if not is_allowed_channel(interaction.channel):
        await interaction.response.send_message(
            "Use this in bid channels only.", ephemeral=True
        )
        return

    if interaction.guild is None or not is_leader(interaction.user, interaction.guild):
        await interaction.response.send_message(
            "Only leaders can close bids.", ephemeral=True
        )
        return

    channel = interaction.channel
    if channel is None:
        await interaction.response.send_message("Channel not found.", ephemeral=True)
        return

    state = get_state(channel.id)
    if state is None:
        await interaction.response.send_message(
            "No open auction found in this thread.", ephemeral=True
        )
        return

    state["phase"] = 3
    state["closed"] = True
    state["closed_announced"] = True
    save_state()

    last_valid = state.get("last_valid_bid")
    if last_valid:
        await interaction.response.send_message(
            f"🔒 Bid closed manually.\n"
            f"Final bid: **{last_valid['toon']} {last_valid['amount']:,}**\n"
            f"Cash out with: `%pay {last_valid['toon']} {last_valid['amount']}`"
        )
    else:
        await interaction.response.send_message(
            "🔒 Bid closed manually. No valid bids recorded."
        )


bot.run(TOKEN)
