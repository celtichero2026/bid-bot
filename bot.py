import os
import json
from datetime import datetime, timezone, timedelta

import discord
from discord.ext import commands, tasks
from discord import app_commands

TOKEN = os.getenv("DISCORD_TOKEN")
LEADER_ROLE_ID = 1415053351116079219
LEADER_ROLE_ID = 1495844307666731069

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


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def utcnow() -> datetime:
    return datetime.now(timezone.utc)


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


def is_leader(member: discord.Member | discord.User | None, guild: discord.Guild | None) -> bool:
    if member is None or guild is None or not isinstance(member, discord.Member):
        return False
    role = guild.get_role(LEADER_ROLE_ID)
    return role in member.roles if role else False


def min_outbid_from_min_bid(min_bid: int) -> int:
    return max(1, int(min_bid * OUTBID_INCREMENT))


# ──────────────────────────────────────────────────────────────────────────────
# Persistent state
# ──────────────────────────────────────────────────────────────────────────────

# Shape:
# bid_state = {
#   "thread_id": {
#       "phase": 1|2|3,
#       "phase1_start": iso str,
#       "last_bid_time": iso str,
#       "phase1_bidders": [user_id, ...],
#       "current_bid": int,
#       "current_toon": str,
#       "current_bidder_id": int,
#       "min_bid": int,
#       "outbid_inc": int,
#       "closed": bool,
#       "phase2_announced": bool,
#       "closed_announced": bool,
#       "last_valid_bid": {
#           "toon": str,
#           "amount": int,
#           "bidder_id": int,
#           "message_id": int | None,
#           "timestamp": iso str,
#       } | None,
#       "bid_log": [
#           {
#               "toon": str,
#               "amount": int,
#               "bidder_id": int,
#               "message_id": int | None,
#               "timestamp": iso str,
#               "valid": bool,
#               "reason": str | None,
#           }
#       ]
#   }
# }
bid_state: dict[int, dict] = {}


def save_state() -> None:
    ensure_data_dir()
    serializable = {str(k): v for k, v in bid_state.items()}
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(serializable, f, indent=2)


def load_state() -> None:
    global bid_state
    ensure_data_dir()
    if not os.path.exists(DATA_FILE):
        bid_state = {}
        return

    with open(DATA_FILE, "r", encoding="utf-8") as f:
        raw = json.load(f)

    bid_state = {int(k): v for k, v in raw.items()}


def get_state(thread_id: int) -> dict | None:
    return bid_state.get(thread_id)


def init_state(thread_id: int, toon: str, amount: int, min_bid: int, bidder_id: int, message_id: int | None) -> dict:
    now = utcnow()
    outbid_inc = min_outbid_from_min_bid(min_bid)

    state = {
        "phase": 1,
        "phase1_start": dt_to_str(now),
        "last_bid_time": dt_to_str(now),
        "phase1_bidders": [bidder_id],
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
    save_state()
    return state


def add_bid_log(
    state: dict,
    toon: str,
    amount: int,
    bidder_id: int,
    message_id: int | None,
    valid: bool,
    reason: str | None = None,
) -> None:
    state["bid_log"].append(
        {
            "toon": toon,
            "amount": amount,
            "bidder_id": bidder_id,
            "message_id": message_id,
            "timestamp": dt_to_str(utcnow()),
            "valid": valid,
            "reason": reason,
        }
    )
    save_state()


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
            save_state()
            return

    state["last_valid_bid"] = None
    state["current_toon"] = ""
    state["current_bid"] = 0
    state["current_bidder_id"] = 0
    save_state()


def phase_label(phase: int) -> str:
    return {
        1: "Phase 1 — Open",
        2: "Phase 2 — Restricted",
        3: "Closed",
    }.get(phase, "Unknown")


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

    # Allow the first human message in the thread after creation
    # so the opening forum/thread post with image/text is not flagged.
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

    try:
        await message.add_reaction("❌")
    except (discord.Forbidden, discord.HTTPException):
        pass

    try:
        await channel.send(
            f"{message.author.mention} Please keep this thread clean. "
            "Use `/bid` to bid or `/review` if something needs leader attention.",
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

    for thread_id, state in list(bid_state.items()):
        if state.get("closed") or state.get("phase") == 3:
            continue

        thread = bot.get_channel(thread_id)
        if thread is None:
            try:
                thread = await bot.fetch_channel(thread_id)
            except discord.NotFound:
                continue
            except discord.Forbidden:
                continue
            except discord.HTTPException:
                continue

        phase1_start = str_to_dt(state["phase1_start"])
        last_bid_time = str_to_dt(state["last_bid_time"])

        if phase1_start is None or last_bid_time is None:
            continue

        # Move to phase 2 after 24h
        if state["phase"] == 1 and now >= phase1_start + timedelta(hours=24):
            state["phase"] = 2

            if not state["phase2_announced"]:
                bidders = state.get("phase1_bidders", [])
                if bidders:
                    mentions = " ".join(f"<@{uid}>" for uid in bidders)
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
                        "No valid phase 1 bidders were recorded."
                    )

                state["phase2_announced"] = True
                save_state()

        # Close 12h after last valid bid once in phase 2
        if state["phase"] == 2 and now >= last_bid_time + timedelta(hours=12):
            state["phase"] = 3
            state["closed"] = True

            if not state["closed_announced"]:
                last_valid = state.get("last_valid_bid")
                if last_valid:
                    toon = last_valid["toon"]
                    amount = last_valid["amount"]
                    await thread.send(
                        "🔒 **Bidding Closed**\n"
                        f"Final bid: **{toon}** — **{amount:,}**\n"
                        f"Moderators: `%pay {toon} {amount}`"
                    )
                else:
                    await thread.send("🔒 **Bidding Closed** — No valid bids recorded.")

                try:
                    if isinstance(thread, discord.Thread):
                        await thread.edit(locked=True)
                except discord.Forbidden:
                    pass
                except discord.HTTPException:
                    pass

                state["closed_announced"] = True
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
async def open_bid(interaction: discord.Interaction, toon: str, amount: int, min_bid: int):
    channel = interaction.channel

    if not is_allowed_channel(channel):
        await interaction.response.send_message("Use this in bid channels only.", ephemeral=True)
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


@bot.tree.command(name="bid", description="Place an outbid")
@app_commands.describe(
    toon="The toon name you are bidding on",
    amount="Your bid amount",
)
async def bid(interaction: discord.Interaction, toon: str, amount: int):
    channel = interaction.channel

    if not is_allowed_channel(channel):
        await interaction.response.send_message("Use this in bid channels only.", ephemeral=True)
        return

    if channel is None:
        await interaction.response.send_message("Channel not found.", ephemeral=True)
        return

    state = get_state(channel.id)
    if state is None:
        await interaction.response.send_message(
            "No bid is open in this thread. Use `/open` first.",
            ephemeral=True,
        )
        return

    if state["phase"] == 3 or state["closed"]:
        await interaction.response.send_message("🔒 Bidding is closed for this item.", ephemeral=True)
        return

    if amount <= 0:
        await interaction.response.send_message("Bid amount must be greater than 0.", ephemeral=True)
        return

    # Phase 2 restriction
    phase1_bidders = state.get("phase1_bidders", [])
    if state["phase"] == 2 and phase1_bidders and interaction.user.id not in phase1_bidders:
        await interaction.response.send_message(
            "⏰ Bidding is in Phase 2 and restricted to users who placed a valid bid in the first 24 hours.",
            ephemeral=True,
        )
        return

    current_bid = state["current_bid"]
    minimum_valid = current_bid + state["outbid_inc"]

    if amount < minimum_valid:
        await interaction.response.send_message(
            f"❌ Invalid bid. Current bid is **{current_bid:,}**. "
            f"Minimum outbid is **{state['outbid_inc']:,}**, so next valid bid is **{minimum_valid:,}**.",
            ephemeral=True,
        )

        # visible invalid record in-thread
        invalid_msg = await channel.send(
            f"❌ Invalid bid by {interaction.user.mention}: {toon} {amount:,}",
            allowed_mentions=discord.AllowedMentions(users=True),
        )
        try:
            await invalid_msg.add_reaction("❌")
        except discord.HTTPException:
            pass

        add_bid_log(
            state=state,
            toon=toon,
            amount=amount,
            bidder_id=interaction.user.id,
            message_id=invalid_msg.id,
            valid=False,
            reason=f"Below required minimum valid bid of {minimum_valid}",
        )
        return

    previous_bidder_id = state.get("current_bidder_id")
    mentions = [interaction.user.mention]

    if previous_bidder_id and previous_bidder_id != interaction.user.id:
        mentions.append(f"<@{previous_bidder_id}>")

    await interaction.response.send_message(
        f"{toon} {amount:,} {' '.join(mentions)}",
        allowed_mentions=discord.AllowedMentions(users=True),
    )

    sent = await interaction.original_response()

    state["current_bid"] = amount
    state["current_toon"] = toon
    state["current_bidder_id"] = interaction.user.id
    state["last_bid_time"] = dt_to_str(utcnow())

    if state["phase"] == 1 and interaction.user.id not in state["phase1_bidders"]:
        state["phase1_bidders"].append(interaction.user.id)

    state["last_valid_bid"] = {
        "toon": toon,
        "amount": amount,
        "bidder_id": interaction.user.id,
        "message_id": sent.id,
        "timestamp": dt_to_str(utcnow()),
    }

    add_bid_log(
        state=state,
        toon=toon,
        amount=amount,
        bidder_id=interaction.user.id,
        message_id=sent.id,
        valid=True,
        reason=None,
    )
    save_state()


@bot.tree.command(name="review", description="Flag a concern for leaders")
@app_commands.describe(reason="Briefly describe the issue")
async def review(interaction: discord.Interaction, reason: str):
    if not is_allowed_channel(interaction.channel):
        await interaction.response.send_message("Use this in bid channels only.", ephemeral=True)
        return

    if interaction.guild is None:
        await interaction.response.send_message("Guild not found.", ephemeral=True)
        return

    role = interaction.guild.get_role(LEADER_ROLE_ID)
    if role is None:
        await interaction.response.send_message("Leader role not found.", ephemeral=True)
        return

    await interaction.response.send_message(
        f"{role.mention} Review requested by {interaction.user.mention}: {reason}",
        allowed_mentions=discord.AllowedMentions(roles=True, users=True),
    )


@bot.tree.command(name="pay", description="Generate %pay from the last valid bid")
async def pay(interaction: discord.Interaction):
    if not is_allowed_channel(interaction.channel):
        await interaction.response.send_message("Use this in bid channels only.", ephemeral=True)
        return

    if interaction.guild is None or not is_leader(interaction.user, interaction.guild):
        await interaction.response.send_message("Only leaders can use `/pay`.", ephemeral=True)
        return

    channel = interaction.channel
    if channel is None:
        await interaction.response.send_message("Channel not found.", ephemeral=True)
        return

    state = get_state(channel.id)
    if state is None:
        await interaction.response.send_message("No open auction found in this thread.", ephemeral=True)
        return

    last_valid = state.get("last_valid_bid")
    if not last_valid:
        await interaction.response.send_message("No valid bid found.", ephemeral=True)
        return

    await interaction.response.send_message(f"%pay {last_valid['toon']} {last_valid['amount']}")


@bot.tree.command(name="bidinfo", description="Show current bid info for this thread")
async def bidinfo(interaction: discord.Interaction):
    if not is_allowed_channel(interaction.channel):
        await interaction.response.send_message("Use this in bid channels only.", ephemeral=True)
        return

    channel = interaction.channel
    if channel is None:
        await interaction.response.send_message("Channel not found.", ephemeral=True)
        return

    state = get_state(channel.id)
    if state is None:
        await interaction.response.send_message("No bid is open in this thread.", ephemeral=True)
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
    bidder_count = len(state.get("phase1_bidders", []))

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


@bot.tree.command(name="setminbid", description="Change the minimum bid for this thread")
@app_commands.describe(min_bid="Corrected minimum bid")
async def setminbid(interaction: discord.Interaction, min_bid: int):
    if not is_allowed_channel(interaction.channel):
        await interaction.response.send_message("Use this in bid channels only.", ephemeral=True)
        return

    if interaction.guild is None or not is_leader(interaction.user, interaction.guild):
        await interaction.response.send_message("Only leaders can adjust the minimum bid.", ephemeral=True)
        return

    channel = interaction.channel
    if channel is None:
        await interaction.response.send_message("Channel not found.", ephemeral=True)
        return

    state = get_state(channel.id)
    if state is None:
        await interaction.response.send_message("No open auction found in this thread.", ephemeral=True)
        return

    if min_bid <= 0:
        await interaction.response.send_message("Minimum bid must be greater than 0.", ephemeral=True)
        return

    state["min_bid"] = min_bid
    state["outbid_inc"] = min_outbid_from_min_bid(min_bid)
    save_state()

    await interaction.response.send_message(
        f"✏️ Min bid updated to **{min_bid:,}**. "
        f"Min outbid is now **{state['outbid_inc']:,}**."
    )


@bot.tree.command(name="invalidate_lastbid", description="Invalidate the last valid bid")
@app_commands.describe(reason="Why the last valid bid is being invalidated")
async def invalidate_lastbid(interaction: discord.Interaction, reason: str):
    if not is_allowed_channel(interaction.channel):
        await interaction.response.send_message("Use this in bid channels only.", ephemeral=True)
        return

    if interaction.guild is None or not is_leader(interaction.user, interaction.guild):
        await interaction.response.send_message("Only leaders can invalidate bids.", ephemeral=True)
        return

    channel = interaction.channel
    if channel is None:
        await interaction.response.send_message("Channel not found.", ephemeral=True)
        return

    state = get_state(channel.id)
    if state is None:
        await interaction.response.send_message("No open auction found in this thread.", ephemeral=True)
        return

    last_valid = state.get("last_valid_bid")
    if not last_valid:
        await interaction.response.send_message("No valid bid found to invalidate.", ephemeral=True)
        return

    target_message_id = last_valid.get("message_id")
    invalidated = False

    for entry in reversed(state["bid_log"]):
        if entry.get("valid"):
            entry["valid"] = False
            entry["reason"] = f"Invalidated by leader: {reason}"
            invalidated = True
            break

    if not invalidated:
        await interaction.response.send_message("Could not invalidate last valid bid.", ephemeral=True)
        return

    if target_message_id:
        try:
            msg = await channel.fetch_message(target_message_id)
            await msg.add_reaction("❌")
        except discord.HTTPException:
            pass

    recalc_last_valid_bid(state)
    save_state()

    new_last = state.get("last_valid_bid")
    if new_last:
        await interaction.response.send_message(
            f"❌ Last valid bid invalidated.\n"
            f"New last valid bid: **{new_last['toon']} {new_last['amount']:,}**"
        )
    else:
        await interaction.response.send_message(
            "❌ Last valid bid invalidated. There are no remaining valid bids."
        )


@bot.tree.command(name="closebid", description="Force close the current bid thread")
async def closebid(interaction: discord.Interaction):
    if not is_allowed_channel(interaction.channel):
        await interaction.response.send_message("Use this in bid channels only.", ephemeral=True)
        return

    if interaction.guild is None or not is_leader(interaction.user, interaction.guild):
        await interaction.response.send_message("Only leaders can close bids.", ephemeral=True)
        return

    channel = interaction.channel
    if channel is None:
        await interaction.response.send_message("Channel not found.", ephemeral=True)
        return

    state = get_state(channel.id)
    if state is None:
        await interaction.response.send_message("No open auction found in this thread.", ephemeral=True)
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
            f"Moderators: `%pay {last_valid['toon']} {last_valid['amount']}`"
        )
    else:
        await interaction.response.send_message("🔒 Bid closed manually. No valid bids recorded.")

    if isinstance(channel, discord.Thread):
        try:
            await channel.edit(locked=True)
        except discord.HTTPException:
            pass


bot.run(TOKEN)
