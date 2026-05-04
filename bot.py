import os
import json
import traceback
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


def is_leader(
    member: discord.Member | discord.User | None, guild: discord.Guild | None
) -> bool:
    if member is None or guild is None:
        return False
    if not isinstance(member, discord.Member):
        return False
    return any(role.id in LEADER_ROLE_IDS for role in member.roles)


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
    return max(1, int(min_bid * OUTBID_INCREMENT))


def phase_label(phase: int) -> str:
    return {
        1: "Phase 1 — Open",
        2: "Phase 2 — Restricted",
        3: "Closed",
    }.get(phase, "Unknown")


def serialize_state() -> dict:
    payload = {}
    for thread_id, state in bid_state.items():
        copy_state = dict(state)
        copy_state["phase1_bidders"] = list(state.get("phase1_bidders", set()))
        copy_state["opted_out_bidders"] = list(state.get("opted_out_bidders", set()))
        payload[str(thread_id)] = copy_state
    return payload


def deserialize_state(raw: dict) -> dict[int, dict]:
    restored = {}
    for thread_id_str, state in raw.items():
        restored[int(thread_id_str)] = {
            **state,
            "phase1_bidders": set(state.get("phase1_bidders", [])),
            "opted_out_bidders": set(state.get("opted_out_bidders", [])),
        }
    return restored


# ──────────────────────────────────────────────────────────────────────────────
# Persistent state
# ──────────────────────────────────────────────────────────────────────────────


def save_state() -> None:
    ensure_data_dir()
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(serialize_state(), f, indent=2)


def load_state() -> None:
    global bid_state
    ensure_data_dir()

    if not os.path.exists(DATA_FILE):
        bid_state = {}
        return

    with open(DATA_FILE, "r", encoding="utf-8") as f:
        raw = json.load(f)

    bid_state = deserialize_state(raw)


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
# Bot lifecycle
# ──────────────────────────────────────────────────────────────────────────────


@bot.event
async def on_ready():
    load_state()
    await bot.tree.sync()
    if not phase_checker.is_running():
        phase_checker.start()
    print(f"Logged in as {bot.user}")


# ──────────────────────────────────────────────────────────────────────────────
# Thread chat discouragement
# ──────────────────────────────────────────────────────────────────────────────


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        await bot.process_commands(message)
        return

    channel = message.channel

    if not isinstance(channel, discord.Thread):
        await bot.process_commands(message)
        return

    if not is_allowed_channel(channel):
        await bot.process_commands(message)
        return

    state = get_state(channel.id)
    if state is None:
        await bot.process_commands(message)
        return

    # ✅ Allow normal comments from leaders
    if is_leader(message.author, message.guild):
        await bot.process_commands(message)
        return

    content = (message.content or "").strip().lower()

    # Allow payout and approved utility commands
    ALLOWED_THREAD_PREFIXES = (
        "%pay",
        "%undo",
        "%refund",
    )

    if any(content.startswith(prefix) for prefix in ALLOWED_THREAD_PREFIXES):
        await bot.process_commands(message)
        return

    # Allow first human message (thread starter)
    try:
        human_messages = []
        async for msg in channel.history(oldest_first=True, limit=20):
            if not msg.author.bot:
                human_messages.append(msg.id)

        if human_messages and message.id == human_messages[0]:
            await bot.process_commands(message)
            return
    except discord.HTTPException:
        pass

    # ❌ React to invalid chatter
    try:
        await message.add_reaction("❌")
    except (discord.Forbidden, discord.HTTPException):
        pass

    # Warning message
    try:
        await channel.send(
            f"{message.author.mention} Please keep this thread clean. "
            "Use `/bid` to bid, `/review` for concerns, `/out` to stop future mentions, or approved mod payout commands.",
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
        if state.get("closed") or state.get("phase") == 3:
            continue

        thread = bot.get_channel(thread_id)
        if thread is None:
            try:
                thread = await bot.fetch_channel(thread_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                continue

        phase1_start = str_to_dt(state["phase1_start"])
        last_bid_time = str_to_dt(state["last_bid_time"])

        if phase1_start is None or last_bid_time is None:
            continue

        if state["phase"] == 1 and now >= phase1_start + timedelta(hours=24):
            state["phase"] = 2
            dirty = True

            if not state["phase2_announced"]:
                bidders = state.get("phase1_bidders", set())
                opted_out = state.get("opted_out_bidders", set())
                mentions = " ".join(
                    f"<@{uid}>" for uid in bidders if uid not in opted_out
                )

                if mentions:
                    msg = (
                        "⏰ **Phase 2 — Restricted Bidding**\n"
                        "Only users who placed a valid bid in the first 24 hours can keep bidding.\n"
                        f"{mentions}"
                    )
                    await thread.send(
                        msg,
                        allowed_mentions=discord.AllowedMentions(users=True),
                    )
                else:
                    await thread.send(
                        "⏰ **Phase 2 — Restricted Bidding**\n"
                        "No active phase 1 bidders remain for mentions."
                    )

                state["phase2_announced"] = True
                dirty = True

        if state["phase"] == 2 and now >= last_bid_time + timedelta(hours=12):
            state["phase"] = 3
            state["closed"] = True
            dirty = True

            if not state["closed_announced"]:
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
                    await thread.send("🔒 **Bidding Closed** — No valid bids recorded.")

                try:
                    if isinstance(thread, discord.Thread):
                        await thread.edit(locked=True)
                except (discord.Forbidden, discord.HTTPException):
                    pass

                state["closed_announced"] = True
                dirty = True

    if dirty:
        save_state()


@phase_checker.before_loop
async def before_phase_checker():
    await bot.wait_until_ready()


# ──────────────────────────────────────────────────────────────────────────────
# Commands
# ──────────────────────────────────────────────────────────────────────────────


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

    if min_bid <= 0 or amount <= 0:
        await interaction.response.send_message(
            "Amounts must be greater than 0.",
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

        if amount <= 0:
            await reply("Bid must be greater than 0.")
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


@bot.tree.command(
    name="out", description="Opt out of future bid mentions in this thread"
)
async def out(interaction: discord.Interaction):
    channel = interaction.channel

    if not isinstance(channel, discord.Thread):
        await interaction.response.send_message(
            "Use this inside a bid thread.", ephemeral=True
        )
        return

    if not is_allowed_channel(channel):
        await interaction.response.send_message(
            "Use this in bid channels only.", ephemeral=True
        )
        return

    state = get_state(channel.id)
    if state is None:
        await interaction.response.send_message(
            "No bid is open in this thread.", ephemeral=True
        )
        return

    state.setdefault("opted_out_bidders", set()).add(interaction.user.id)
    save_state()

    await interaction.response.send_message(
        "You are out. You will not be mentioned in future bid updates for this thread.",
        ephemeral=True,
    )


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

    if min_bid <= 0:
        await interaction.response.send_message(
            "Minimum bid must be greater than 0.", ephemeral=True
        )
        return

    state["min_bid"] = min_bid
    state["outbid_inc"] = min_outbid_from_min_bid(min_bid)
    save_state()

    await interaction.response.send_message(
        f"✏️ Min bid updated to **{min_bid:,}**. "
        f"Min outbid is now **{state['outbid_inc']:,}**."
    )


@bot.tree.command(name="correctbid", description="Correct the amount on a specific bid")
@app_commands.describe(
    bid_number="The bid number to correct",
    amount="The corrected bid amount",
    reason="Why this bid is being corrected",
)
async def correctbid(
    interaction: discord.Interaction, bid_number: int, amount: int, reason: str
):
    if not is_allowed_channel(interaction.channel):
        await interaction.response.send_message(
            "Use this in bid channels only.", ephemeral=True
        )
        return

    if interaction.guild is None or not is_leader(interaction.user, interaction.guild):
        await interaction.response.send_message(
            "Only leaders can correct bids.", ephemeral=True
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

    if amount <= 0:
        await interaction.response.send_message(
            "Corrected amount must be greater than 0.", ephemeral=True
        )
        return

    target = None

    for entry in state.get("bid_log", []):
        if entry.get("bid_number") == bid_number:
            target = entry
            break

    if target is None:
        await interaction.response.send_message(
            f"No bid found with bid number **#{bid_number}**.", ephemeral=True
        )
        return

    if not target.get("valid"):
        await interaction.response.send_message(
            f"Bid **#{bid_number}** is invalid. Invalidate corrections should stay separate.",
            ephemeral=True,
        )
        return

    old_amount = target["amount"]

    # Check previous valid bid before this one
    previous_valid = None
    for entry in state.get("bid_log", []):
        if entry.get("bid_number") == bid_number:
            break
        if entry.get("valid"):
            previous_valid = entry

    min_outbid = state["outbid_inc"]

    if previous_valid:
        required = previous_valid["amount"] + min_outbid
        if amount < required:
            await interaction.response.send_message(
                f"❌ Corrected amount is too low.\n"
                f"Previous valid bid was **{previous_valid['amount']:,}**.\n"
                f"Corrected bid must be at least **{required:,}**.",
                ephemeral=True,
            )
            return
    else:
        min_bid = state["min_bid"]
        if amount < min_bid:
            await interaction.response.send_message(
                f"❌ Corrected amount is below the minimum bid of **{min_bid:,}**.",
                ephemeral=True,
            )
            return

    target["amount"] = amount
    target["corrected"] = True
    target["correction_reason"] = reason
    target["old_amount"] = old_amount

    target_message_id = target.get("message_id")
    if target_message_id:
        try:
            msg = await channel.fetch_message(target_message_id)
            await msg.add_reaction("✏️")
        except discord.HTTPException:
            pass

    recalc_last_valid_bid(state)
    recalc_phase1_bidders(state)
    save_state()

    new_last = state.get("last_valid_bid")

    await interaction.response.send_message(
        f"✏️ Bid **#{bid_number}** corrected.\n"
        f"Old amount: **{old_amount:,}**\n"
        f"New amount: **{amount:,}**\n"
        f"Reason: {reason}\n"
        f"Current valid bid: **{new_last['toon']} {new_last['amount']:,}**"
    )


@bot.tree.command(
    name="invalidate", description="Invalidate a specific bid by bid number"
)
@app_commands.describe(
    bid_number="The bid number to invalidate",
    reason="Why this bid is being invalidated",
)
async def invalidate(interaction: discord.Interaction, bid_number: int, reason: str):
    if not is_allowed_channel(interaction.channel):
        await interaction.response.send_message(
            "Use this in bid channels only.", ephemeral=True
        )
        return

    if interaction.guild is None or not is_leader(interaction.user, interaction.guild):
        await interaction.response.send_message(
            "Only leaders can invalidate bids.", ephemeral=True
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

    target = None

    for entry in state.get("bid_log", []):
        if entry.get("bid_number") == bid_number:
            target = entry
            break

    if target is None:
        await interaction.response.send_message(
            f"No bid found with bid number **#{bid_number}**.", ephemeral=True
        )
        return

    if not target.get("valid"):
        await interaction.response.send_message(
            f"Bid **#{bid_number}** is already invalid.", ephemeral=True
        )
        return

    target["valid"] = False
    target["reason"] = f"Invalidated by leader: {reason}"

    target_message_id = target.get("message_id")
    if target_message_id:
        try:
            msg = await channel.fetch_message(target_message_id)
            await msg.add_reaction("❌")
        except discord.HTTPException:
            pass

    recalc_last_valid_bid(state)
    recalc_phase1_bidders(state)
    save_state()

    new_last = state.get("last_valid_bid")

    if new_last:
        await interaction.response.send_message(
            f"❌ Bid **#{bid_number}** invalidated.\n"
            f"Reason: {reason}\n"
            f"Current valid bid: **{new_last['toon']} {new_last['amount']:,}**"
        )
    else:
        await interaction.response.send_message(
            f"❌ Bid **#{bid_number}** invalidated.\n"
            f"Reason: {reason}\n"
            "There are no remaining valid bids."
        )


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

    if isinstance(channel, discord.Thread):
        try:
            await channel.edit(locked=True)
        except discord.HTTPException:
            pass


bot.run(TOKEN)
