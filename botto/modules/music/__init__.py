import asyncio
import datetime
import itertools
import math
import random
import re
from typing import Union

import discord  # type: ignore
from discord.ext import commands  # type: ignore
import humanize  # type: ignore
import wavelink  # type: ignore

import botto
from .models import Player, Track

RURL = re.compile(r"https?:\/\/(?:www\.)?.+")


class Music(commands.Cog):
    """Our main Music Cog."""

    def __init__(self, bot: Union[commands.Bot, commands.AutoShardedBot]):
        self.bot = bot

        if not hasattr(bot, "wavelink"):
            self.bot.wavelink = wavelink.Client(bot)

        bot.loop.create_task(self.initiate_nodes())

    async def initiate_nodes(self):
        nodes = {
            "MAIN": {
                "host": "127.0.0.1",
                "port": 2333,
                "rest_url": "http://127.0.0.1:2333",
                "password": "youshallnotpass",
                "identifier": "MAIN",
                "region": "us_central",
            }
        }

        for n in nodes.values():
            node = await self.bot.wavelink.initiate_node(
                host=n["host"],
                port=n["port"],
                rest_uri=n["rest_url"],
                password=n["password"],
                identifier=n["identifier"],
                region=n["region"],
                secure=False,
            )

            node.set_hook(self.event_hook)

    def event_hook(self, event) -> None:
        """Our event hook. Dispatched when an event occurs on our Node."""
        if isinstance(event, wavelink.TrackEnd):
            event.player.next_event.set()
        elif isinstance(event, wavelink.TrackException):
            print(event.error)

    def required(self, player, invoked_with):
        """Calculate required votes."""
        channel = self.bot.get_channel(int(player.channel_id))
        if invoked_with == "stop":
            if len(channel.members) - 1 == 2:
                return 2

        return math.ceil((len(channel.members) - 1) / 2.5)

    async def has_perms(self, ctx, **perms):
        """Check whether a member has the given permissions."""
        player = self.bot.wavelink.get_player(ctx.guild.id, cls=Player)

        if ctx.author.id == player.dj.id:
            return True

        ch = ctx.channel
        permissions = ch.permissions_for(ctx.author)

        missing = [
            perm
            for perm, value in perms.items()
            if getattr(permissions, perm, None) != value
        ]

        if not missing:
            return True

        return False

    async def vote_check(self, ctx, command: str):
        player = self.bot.wavelink.get_player(ctx.guild.id, cls=Player)

        vcc = len(self.bot.get_channel(int(player.channel_id)).members) - 1
        votes = getattr(player, command + "s", None)

        if vcc < 3 and not ctx.invoked_with == "stop":
            votes.clear()
            return True
        else:
            votes.add(ctx.author.id)

            if len(votes) >= self.required(player, ctx.invoked_with):
                votes.clear()
                return True
        return False

    async def do_vote(self, ctx, player, command: str):
        attr = getattr(player, command + "s", None)
        player = self.bot.wavelink.get_player(ctx.guild.id, cls=Player)

        if ctx.author.id in attr:
            await ctx.send(
                f"{ctx.author.mention}, you have already voted to {command}!",
                delete_after=15,
            )
        elif await self.vote_check(ctx, command):
            await ctx.send(f"Vote request for {command} passed!", delete_after=20)
            to_do = getattr(self, f"do_{command}")
            await to_do(ctx)
        else:
            await ctx.send(
                f"{ctx.author.mention}, has voted to {command} the song!"
                f" **{self.required(player, ctx.invoked_with) - len(attr)}** more votes needed!",
                delete_after=45,
            )

    @botto.command(name="reactcontrol", hidden=True)
    async def react_control(self, ctx):
        """Dummy command for error handling in our player."""
        pass

    @botto.command(name="connect", aliases=["join"])
    async def connect_(self, ctx, *, channel: discord.VoiceChannel = None):
        """Connect to voice.
        Parameters
        ------------
        channel: discord.VoiceChannel [Optional]
            The channel to connect to. If a channel is not specified, an attempt to join the voice channel you are in
            will be made.
        """
        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass

        if not channel:
            try:
                channel = ctx.author.voice.channel
            except AttributeError:
                raise discord.DiscordException(
                    "No channel to join. Please either specify a valid channel or join one."
                )

        player = self.bot.wavelink.get_player(ctx.guild.id, cls=Player)

        if player.is_connected:
            if ctx.author.voice.channel == ctx.guild.me.voice.channel:
                return

        await player.connect(channel.id)

    @botto.command(name="play", aliases=["sing"])
    @commands.cooldown(1, 2, commands.BucketType.user)
    async def play_(self, ctx, *, query: str):
        """Queue a song or playlist for playback.
        Aliases
        ---------
            sing
        Parameters
        ------------
        query: simple, URL [Required]
            The query to search for a song. This could be a simple search term or a valid URL.
            e.g Youtube URL or Spotify Playlist URL.
        Examples
        ----------
        <prefix>play <query>
            {ctx.prefix}play What is love?
            {ctx.prefix}play https://www.youtube.com/watch?v=XfR9iY5y94s
        """
        await ctx.trigger_typing()

        await ctx.invoke(self.connect_)
        query = query.strip("<>")

        player = self.bot.wavelink.get_player(ctx.guild.id, cls=Player)

        if not player.is_connected:
            return await ctx.send(
                "Bot is not connected to voice. Please join a voice channel to play music."
            )

        if not player.dj:
            player.dj = ctx.author

        if not RURL.match(query):
            query = f"ytsearch:{query}"

        tracks = await self.bot.wavelink.get_tracks(query)
        if not tracks:
            return await ctx.send(
                "No songs were found with that query. Please try again."
            )

        if isinstance(tracks, wavelink.TrackPlaylist):
            for t in tracks.tracks:
                await player.queue.put(Track(t.id, t.info, ctx=ctx))

            await ctx.send(
                f'```ini\nAdded the playlist {tracks.data["playlistInfo"]["name"]}'
                f" with {len(tracks.tracks)} songs to the queue.\n```"
            )
        else:
            track = tracks[0]
            await ctx.send(
                f"```ini\nAdded {track.title} to the Queue\n```", delete_after=15
            )
            await player.queue.put(Track(track.id, track.info, ctx=ctx))

        if player.controller_message and player.is_playing:
            await player.invoke_controller()

    @botto.command(name="now_playing", aliases=["np", "current", "currentsong"])
    @commands.cooldown(2, 15, commands.BucketType.user)
    async def now_playing(self, ctx):
        """Invoke the player controller.
        Aliases
        ---------
            np
            current
            currentsong
        Examples
        ----------
        <prefix>now_playing
            {ctx.prefix}np
        The player controller contains various information about the current and upcoming songs.
        """
        player = self.bot.wavelink.get_player(ctx.guild.id, cls=Player)
        if not player:
            return

        if not player.is_connected:
            return

        if player.updating or player.update:
            return

        await player.invoke_controller()

    @botto.command(name="pause")
    async def pause_(self, ctx):
        """Pause the currently playing song.
        Examples
        ----------
        <prefix>pause
            {ctx.prefix}pause
        """
        player = self.bot.wavelink.get_player(ctx.guild.id, cls=Player)
        if not player:
            return

        if not player.is_connected:
            await ctx.send("I am not currently connected to voice!")

        if player.paused:
            return

        if await self.has_perms(ctx, manage_guild=True):
            await ctx.send(
                f"{ctx.author.mention} has paused the song as an admin or DJ.",
                delete_after=25,
            )
            return await self.do_pause(ctx)

        await self.do_vote(ctx, player, "pause")

    async def do_pause(self, ctx):
        player = self.bot.wavelink.get_player(ctx.guild.id, cls=Player)
        player.paused = True
        await player.set_pause(True)

    @botto.command(name="resume")
    async def resume_(self, ctx):
        """Resume a currently paused song.
        Examples
        ----------
        <prefix>resume
            {ctx.prefix}resume
        """
        player = self.bot.wavelink.get_player(ctx.guild.id, cls=Player)

        if not player.is_connected:
            await ctx.send("I am not currently connected to voice!")

        if not player.paused:
            return

        if await self.has_perms(ctx, manage_guild=True):
            await ctx.send(
                f"{ctx.author.mention} has resumed the song as an admin or DJ.",
                delete_after=25,
            )
            return await self.do_resume(ctx)

        await self.do_vote(ctx, player, "resume")

    async def do_resume(self, ctx):
        player = self.bot.wavelink.get_player(ctx.guild.id, cls=Player)
        await player.set_pause(False)

    @botto.command(name="skip")
    @commands.cooldown(5, 10, commands.BucketType.user)
    async def skip_(self, ctx):
        """Skip the current song.
        Examples
        ----------
        <prefix>skip
            {ctx.prefix}skip
        """
        player = self.bot.wavelink.get_player(ctx.guild.id, cls=Player)

        if not player.is_connected:
            return await ctx.send("I am not currently connected to voice!")

        if await self.has_perms(ctx, manage_guild=True):
            await ctx.send(
                f"{ctx.author.mention} has skipped the song as an admin or DJ.",
                delete_after=25,
            )
            return await self.do_skip(ctx)

        if player.current.requester.id == ctx.author.id:
            await ctx.send(f"The requester {ctx.author.mention} has skipped the song.")
            return await self.do_skip(ctx)

        await self.do_vote(ctx, player, "skip")

    async def do_skip(self, ctx):
        player = self.bot.wavelink.get_player(ctx.guild.id, cls=Player)

        await player.stop()

    @botto.command(name="stop")
    @commands.cooldown(3, 30, commands.BucketType.guild)
    async def stop_(self, ctx):
        """Stop the player, disconnect and clear the queue.
        Examples
        ----------
        <prefix>stop
            {ctx.prefix}stop
        """
        player = self.bot.wavelink.get_player(ctx.guild.id, cls=Player)

        if not player.is_connected:
            return await ctx.send("I am not currently connected to voice!")

        if await self.has_perms(ctx, manage_guild=True):
            await ctx.send(
                f"{ctx.author.mention} has stopped the player as an admin or DJ.",
                delete_after=25,
            )
            return await self.do_stop(ctx)

        await self.do_vote(ctx, player, "stop")

    async def do_stop(self, ctx):
        player = self.bot.wavelink.get_player(ctx.guild.id, cls=Player)

        await player.destroy_controller()
        await player.disconnect()

    @botto.command(name="volume", aliases=["vol"])
    @commands.cooldown(1, 2, commands.BucketType.guild)
    async def volume_(self, ctx, *, value: int):
        """Change the player volume.
        Aliases
        ---------
            vol
        Parameters
        ------------
        value: [Required]
            The volume level you would like to set. This can be a number between 1 and 100.
        Examples
        ----------
        <prefix>volume <value>
            {ctx.prefix}volume 50
        """
        player = self.bot.wavelink.get_player(ctx.guild.id, cls=Player)

        if not player.is_connected:
            return await ctx.send("I am not currently connected to voice!")

        if not 0 < value < 101:
            return await ctx.send("Please enter a value between 1 and 100.")

        if (
            not await self.has_perms(ctx, manage_guild=True)
            and player.dj.id != ctx.author.id
        ):
            if (len(player.connected_channel.members) - 1) > 2:
                return

        await player.set_volume(value)
        await ctx.send(f"Set the volume to **{value}**%", delete_after=7)

        if not player.updating and not player.update:
            await player.invoke_controller()

    @botto.command(name="queue", aliases=["q", "que"])
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def queue_(self, ctx):
        """Retrieve a list of currently queued songs.
        Aliases
        ---------
            que
            q
        Examples
        ----------
        <prefix>queue
            {ctx.prefix}queue
            {ctx.prefix}q
        """
        player = self.bot.wavelink.get_player(ctx.guild.id, cls=Player)

        if not player.is_connected:
            return await ctx.send("I am not currently connected to voice!")

        upcoming = list(itertools.islice(player.entries, 0, 10))

        if not upcoming:
            return await ctx.send(
                "```\nNo more songs in the Queue!\n```", delete_after=15
            )

        fmt = "\n".join(f"**`{str(song)}`**" for song in upcoming)
        embed = discord.Embed(title=f"Upcoming - Next {len(upcoming)}", description=fmt)

        await ctx.send(embed=embed)

    @botto.command(name="shuffle", aliases=["mix"])
    @commands.cooldown(2, 10, commands.BucketType.user)
    async def shuffle_(self, ctx):
        """Shuffle the current queue.
        Aliases
        ---------
            mix
        Examples
        ----------
        <prefix>shuffle
            {ctx.prefix}shuffle
            {ctx.prefix}mix
        """
        player = self.bot.wavelink.get_player(ctx.guild.id, cls=Player)

        if not player.is_connected:
            return await ctx.send("I am not currently connected to voice!")

        if len(player.entries) < 3:
            return await ctx.send(
                "Please add more songs to the queue before trying to shuffle.",
                delete_after=10,
            )

        if await self.has_perms(ctx, manage_guild=True):
            await ctx.send(
                f"{ctx.author.mention} has shuffled the playlist as an admin or DJ.",
                delete_after=25,
            )
            return await self.do_shuffle(ctx)

        await self.do_vote(ctx, player, "shuffle")

    async def do_shuffle(self, ctx):
        player = self.bot.wavelink.get_player(ctx.guild.id, cls=Player)
        random.shuffle(player.queue._queue)

        player.update = True

    @botto.command(name="repeat")
    async def repeat_(self, ctx):
        """Repeat the currently playing song.
        Examples
        ----------
        <prefix>repeat
            {ctx.prefix}repeat
        """
        player = self.bot.wavelink.get_player(ctx.guild.id, cls=Player)

        if not player.is_connected:
            return

        if await self.has_perms(ctx, manage_guild=True):
            await ctx.send(
                f"{ctx.author.mention} has repeated the song as an admin or DJ.",
                delete_after=25,
            )
            return await self.do_repeat(ctx)

        await self.do_vote(ctx, player, "repeat")

    async def do_repeat(self, ctx):
        player = self.bot.wavelink.get_player(ctx.guild.id, cls=Player)

        if not player.entries:
            await player.queue.put(player.current)
        else:
            player.queue._queue.appendleft(player.current)

        player.update = True

    @botto.command(name="vol_up", hidden=True)
    async def volume_up(self, ctx):
        player = self.bot.wavelink.get_player(ctx.guild.id, cls=Player)

        if not player.is_connected:
            return

        vol = int(math.ceil((player.volume + 10) / 10)) * 10

        if vol > 100:
            vol = 100
            await ctx.send("Maximum volume reached", delete_after=7)

        await player.set_volume(vol)
        player.update = True

    @botto.command(name="vol_down", hidden=True)
    async def volume_down(self, ctx):
        player = self.bot.wavelink.get_player(ctx.guild.id, cls=Player)

        if not player.is_connected:
            return

        vol = int(math.ceil((player.volume - 10) / 10)) * 10

        if vol < 0:
            vol = 0
            await ctx.send("Player is currently muted", delete_after=10)

        await player.set_volume(vol)
        player.update = True

    @botto.command(name="seteq")
    async def set_eq(self, ctx, *, eq: str):
        player = self.bot.wavelink.get_player(ctx.guild.id, cls=Player)

        if eq.upper() not in player.equalizers:
            return await ctx.send(
                f"`{eq}` - Is not a valid equalizer!\nTry Flat, Boost, Metal, Piano."
            )

        await player.set_preq(eq)
        player.eq = eq.capitalize()
        await ctx.send(f"The player Equalizer was set to - {eq.capitalize()}")

    @botto.command()
    async def info(self, ctx):
        """Retrieve various Node/Server/Player information."""
        player = self.bot.wavelink.get_player(ctx.guild.id, cls=Player)
        node = player.node

        used = humanize.naturalsize(node.stats.memory_used)
        total = humanize.naturalsize(node.stats.memory_allocated)
        free = humanize.naturalsize(node.stats.memory_free)
        cpu = node.stats.cpu_cores

        fmt = (
            f"**WaveLink:** `{wavelink.__version__}`\n\n"
            f"Connected to `{len(self.bot.wavelink.nodes)}` nodes.\n"
            f"Best available Node `{self.bot.wavelink.get_best_node().__repr__()}`\n"
            f"`{len(self.bot.wavelink.players)}` players are distributed on nodes.\n"
            f"`{node.stats.players}` players are distributed on server.\n"
            f"`{node.stats.playing_players}` players are playing on server.\n\n"
            f"Server Memory: `{used}/{total}` | `({free} free)`\n"
            f"Server CPU: `{cpu}`\n\n"
            f"Server Uptime: `{datetime.timedelta(milliseconds=node.stats.uptime)}`"
        )
        await ctx.send(fmt)


def setup(bot: botto.Botto) -> None:
    bot.add_cog(Music(bot))
