from typing import Literal

@bot.tree.command(name="bid", description="Post a bid")
async def bid(
    interaction: discord.Interaction,
    toon: str,
    amount: int,
    priority: Literal["Main", "Alt"] = None,
    m1: discord.Member = None,
    m2: discord.Member = None,
    m3: discord.Member = None,
    m4: discord.Member = None,
    m5: discord.Member = None,
    m6: discord.Member = None,
    m7: discord.Member = None,
    m8: discord.Member = None,
    m9: discord.Member = None,
    m10: discord.Member = None
):
    mentions = [m.mention for m in [m1,m2,m3,m4,m5,m6,m7,m8,m9,m10] if m]

    parts = [toon, str(amount)]

    if priority:
        parts.append(priority)

    if mentions:
        parts.extend(mentions)

    msg = " ".join(parts)

    await interaction.response.send_message(msg)
