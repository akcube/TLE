from PIL import Image, ImageFont, ImageDraw
from tle import constants
from tle.util import db
from tle.util import tasks
from tle.util import table
from tle.util import paginator
from tle.util import events
from tle.util import discord_common
from tle.util import codeforces_common as cf_common
from tle.util import codeforces_api as cf
from tle.util import cache_system2
from discord.ext import commands
import random
import discord
from gi.repository import Pango, PangoCairo
import io
import asyncio
import contextlib
import logging
import math
import html
import cairo
import os
import time
import gi
import requests

gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")

_HANDLES_PER_PAGE = 15
_NAME_MAX_LEN = 20
_PAGINATE_WAIT_TIME = 5 * 60  # 5 minutes
_PRETTY_HANDLES_PER_PAGE = 10
_TOP_DELTAS_COUNT = 5
_UPDATE_HANDLE_STATUS_INTERVAL = 6 * 60 * 60  # 6 hours
_MAX_RATING_CHANGES_PER_EMBED = 15
_MAX_TOP_RANKS = 5


class HandleCogError(commands.CommandError):
    pass


def rating_to_color(rating):
    """returns (r, g, b) pixels values corresponding to rating"""
    # TODO: Integrate these colors with the ranks in codeforces_api.py
    BLACK = (10, 10, 10)
    RED = (255, 20, 20)
    BLUE = (0, 0, 200)
    GREEN = (0, 140, 0)
    ORANGE = (250, 140, 30)
    PURPLE = (160, 0, 120)
    CYAN = (0, 165, 170)
    GREY = (70, 70, 70)
    if rating is None:
        return BLACK
    if rating < 1200:
        return GREY
    if rating < 1400:
        return GREEN
    if rating < 1600:
        return CYAN
    if rating < 1900:
        return BLUE
    if rating < 2100:
        return PURPLE
    if rating < 2400:
        return ORANGE
    return RED


FONTS = [
    "Noto Sans",
    "Noto Sans CJK JP",
    "Noto Sans CJK SC",
    "Noto Sans CJK TC",
    "Noto Sans CJK HK",
    "Noto Sans CJK KR",
]


def get_gudgitters_image(rankings):
    """return PIL image for rankings"""
    SMOKE_WHITE = (250, 250, 250)
    BLACK = (0, 0, 0)

    DISCORD_GRAY = (0.212, 0.244, 0.247)

    ROW_COLORS = ((0.95, 0.95, 0.95), (0.9, 0.9, 0.9))

    WIDTH = 900
    HEIGHT = 450
    BORDER_MARGIN = 20
    COLUMN_MARGIN = 10
    HEADER_SPACING = 1.25
    WIDTH_RANK = 0.08 * WIDTH
    WIDTH_NAME = 0.38 * WIDTH
    LINE_HEIGHT = (HEIGHT - 2 * BORDER_MARGIN) / (10 + HEADER_SPACING)

    # Cairo+Pango setup
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, WIDTH, HEIGHT)
    context = cairo.Context(surface)
    context.set_line_width(1)
    context.set_source_rgb(*DISCORD_GRAY)
    context.rectangle(0, 0, WIDTH, HEIGHT)
    context.fill()
    layout = PangoCairo.create_layout(context)
    layout.set_font_description(
        Pango.font_description_from_string(",".join(FONTS) + " 20")
    )
    layout.set_ellipsize(Pango.EllipsizeMode.END)

    def draw_bg(y, color_index):
        nxty = y + LINE_HEIGHT

        # Simple
        context.move_to(BORDER_MARGIN, y)
        context.line_to(WIDTH, y)
        context.line_to(WIDTH, nxty)
        context.line_to(0, nxty)
        context.set_source_rgb(*ROW_COLORS[color_index])
        context.fill()

    def draw_row(pos, username, handle, rating, color, y, bold=False):
        context.set_source_rgb(*[x / 255.0 for x in color])

        context.move_to(BORDER_MARGIN, y)

        def draw(text, width=-1):
            text = html.escape(text)
            if bold:
                text = f"<b>{text}</b>"
            # pixel = 1000 pango units
            layout.set_width((width - COLUMN_MARGIN) * 1000)
            layout.set_markup(text, -1)
            PangoCairo.show_layout(context, layout)
            context.rel_move_to(width, 0)

        draw(pos, WIDTH_RANK)
        draw(username, WIDTH_NAME)
        draw(handle, WIDTH_NAME)
        draw(rating)

    #

    y = BORDER_MARGIN

    # draw header
    draw_row("#", "Name", "Handle", "Rating", SMOKE_WHITE, y, bold=True)
    y += LINE_HEIGHT * HEADER_SPACING

    for i, (pos, name, handle, rating) in enumerate(rankings):
        color = rating_to_color(rating)
        draw_bg(y, i % 2)
        draw_row(str(pos), name, handle, str(rating), color, y)
        if rating != "N/A" and rating >= 3000:  # nutella
            draw_row("", name[0], handle[0], "", BLACK, y)
        y += LINE_HEIGHT

    image_data = io.BytesIO()
    surface.write_to_png(image_data)
    image_data.seek(0)
    discord_file = discord.File(image_data, filename="gudgitters.png")
    return discord_file


def get_prettyhandles_image(rows, font):
    """return PIL image for rankings"""
    SMOKE_WHITE = (250, 250, 250)
    BLACK = (0, 0, 0)
    img = Image.new("RGB", (900, 450), color=SMOKE_WHITE)
    draw = ImageDraw.Draw(img)

    START_X, START_Y = 20, 20
    Y_INC = 32
    WIDTH_RANK = 64
    WIDTH_NAME = 340

    def draw_row(pos, username, handle, rating, color, y):
        x = START_X
        draw.text((x, y), pos, fill=color, font=font)
        x += WIDTH_RANK
        draw.text((x, y), username, fill=color, font=font)
        x += WIDTH_NAME
        draw.text((x, y), handle, fill=color, font=font)
        x += WIDTH_NAME
        draw.text((x, y), rating, fill=color, font=font)

    y = START_Y
    # draw header
    draw_row("#", "Username", "Handle", "Rating", BLACK, y)
    y += int(Y_INC * 1.5)

    # trim name to fit in the column width
    def _trim(name):
        width = WIDTH_NAME - 10
        while font.getsize(name)[0] > width:
            name = name[:-4] + "..."  # "…" is printed as floating dots
        return name

    for pos, name, handle, rating in rows:
        name = _trim(name)
        handle = _trim(handle)
        color = rating_to_color(rating)
        draw_row(str(pos), name, handle, str(rating) if rating else "N/A", color, y)
        if rating and rating >= 3000:  # nutella
            nutella_x = START_X + WIDTH_RANK
            draw.text((nutella_x, y), name[0], fill=BLACK, font=font)
            nutella_x += WIDTH_NAME
            draw.text((nutella_x, y), handle[0], fill=BLACK, font=font)
        y += Y_INC

    return img


def _make_profile_embed(member, user, *, mode):
    assert mode in ("set", "get")
    if mode == "set":
        desc = f"Handle for {member.mention} successfully set to **[{user.handle}]({user.url})**"
    else:
        desc = f"Handle for {member.mention} is currently set to **[{user.handle}]({user.url})**"
    if user.rating is None:
        embed = discord.Embed(description=desc)
        embed.add_field(name="Rating", value="Unrated", inline=True)
    else:
        embed = discord.Embed(description=desc, color=user.rank.color_embed)
        embed.add_field(name="Rating", value=user.rating, inline=True)
        embed.add_field(name="Rank", value=user.rank.title, inline=True)
    embed.set_thumbnail(url=f"{user.titlePhoto}")
    return embed


def _make_pages(users, title):
    chunks = paginator.chunkify(users, _HANDLES_PER_PAGE)
    pages = []
    done = 0

    style = table.Style("{:>}  {:<}  {:<}  {:<}")
    for chunk in chunks:
        t = table.Table(style)
        t += table.Header("#", "Name", "Handle", "Rating")
        t += table.Line()
        for i, (member, handle, rating) in enumerate(chunk):
            name = member.display_name
            if len(name) > _NAME_MAX_LEN:
                name = name[: _NAME_MAX_LEN - 1] + "…"
            rank = cf.rating2rank(rating)
            rating_str = "N/A" if rating is None else str(rating)
            t += table.Data(i + done, name, handle, f"{rating_str} ({rank.title_abbr})")
        table_str = "```\n" + str(t) + "\n```"
        embed = discord_common.cf_color_embed(description=table_str)
        pages.append((title, embed))
        done += len(chunk)
    return pages


class Handles(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.logger = logging.getLogger(self.__class__.__name__)
        self.font = ImageFont.truetype(
            constants.NOTO_SANS_CJK_BOLD_FONT_PATH, size=26
        )  # font for ;handle pretty

    @commands.Cog.listener()
    async def on_ready(self):
        cf_common.event_sys.add_listener(self._on_rating_changes)
        self._set_ex_users_inactive_task.start()

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        cf_common.user_db.set_inactive([(member.guild.id, member.id)])

    @tasks.task_spec(
        name="SetExUsersInactive",
        waiter=tasks.Waiter.fixed_delay(_UPDATE_HANDLE_STATUS_INTERVAL),
    )
    async def _set_ex_users_inactive_task(self, _):
        # To set users inactive in case the bot was dead when they left.
        to_set_inactive = []
        for guild in self.bot.guilds:
            user_id_handle_pairs = cf_common.user_db.get_handles_for_guild(guild.id)
            to_set_inactive += [
                (guild.id, user_id)
                for user_id, _ in user_id_handle_pairs
                if guild.get_member(user_id) is None
            ]
        cf_common.user_db.set_inactive(to_set_inactive)

    @events.listener_spec(
        name="RatingChangesListener",
        event_cls=events.RatingChangesUpdate,
        with_lock=True,
    )
    async def _on_rating_changes(self, event):
        contest, changes = event.contest, event.rating_changes
        change_by_handle = {change.handle: change for change in changes}

        async def update_for_guild(guild):
            if cf_common.user_db.has_auto_role_update_enabled(guild.id):
                with contextlib.suppress(HandleCogError):
                    await self._update_ranks(guild)
            channel_id = cf_common.user_db.get_rankup_channel(guild.id)
            channel = guild.get_channel(channel_id)
            if channel is not None:
                with contextlib.suppress(HandleCogError):
                    # most likely the ranklist exists
                    ranklist = cf_common.cache2.ranklist_cache.get_ranklist(contest)
                    embeds = self._make_rankup_embed(
                        guild, contest, change_by_handle, ranklist
                    )
                    for embed in embeds:
                        await channel.send(embed=embed)

        await asyncio.gather(
            *(update_for_guild(guild) for guild in self.bot.guilds),
            return_exceptions=True,
        )
        self.logger.info(f"All guilds updated for contest {contest.id}.")

    @commands.group(
        brief="Commands that have to do with handles",
        invoke_without_command=True,
    )
    async def handle(self, ctx):
        """Change or collect information about specific handles on Codeforces"""
        await ctx.send_help(ctx.command)

    @staticmethod
    async def update_member_rank_role(member, role_to_assign, *, reason):
        """Sets the `member` to only have the rank role of `role_to_assign`. All other rank roles
        on the member, if any, will be removed. If `role_to_assign` is None all existing rank roles
        on the member will be removed.
        """
        role_names_to_remove = {rank.title for rank in cf.RATED_RANKS}
        if role_to_assign is not None:
            role_names_to_remove.discard(role_to_assign.name)
        to_remove = [role for role in member.roles if role.name in role_names_to_remove]
        if to_remove:
            await member.remove_roles(*to_remove, reason=reason)
        if role_to_assign is not None and role_to_assign not in member.roles:
            await member.add_roles(role_to_assign, reason=reason)

    @handle.command(brief="Set Codeforces handle of a user")
    @commands.has_any_role("Admin", "Moderator")
    async def set(self, ctx, member: discord.Member, handle: str):
        """Set Codeforces handle of a user."""
        # CF API returns correct handle ignoring case, update to it
        users = await cf.user.info(handles=[handle])
        await self._set(ctx, member, users[0])

    @handle.command(brief="Refreshing the handles of users after cf magic")
    @commands.has_any_role("Admin", "Moderator")
    async def refresh(self, ctx):
        await ctx.send("Please wait...")
        registeredUsers = cf_common.user_db.get_cf_users_for_guild(ctx.guild.id)
        totalUsers = len(registeredUsers)
        done = 0
        for user in registeredUsers:
            discordID = user[0]
            currHandle = user[1].handle
            oldURL = 'https://codeforces.com/profile/' + currHandle
            newHandle = str((requests.get(oldURL).url)).split('/')[-1]
            if newHandle != currHandle:
                users = await cf.user.info(handles=[newHandle])
                try:
                    cf_common.user_db.set_handle(discordID, ctx.guild.id, newHandle)
                except db.UniqueConstraintFailed:
                    raise HandleCogError(
                        f"The handle `{newHandle}` is already associated with another user."
                    )
                cf_common.user_db.cache_cf_user(users[0])
                sleep(0.2)
            done = done + 1
            if done == int(totalUsers/3):
                await ctx.send("Updated 33% handles.")
            if done == int(2*totalUsers/3):
                await ctx.send("Updated 66% handles.")
        embed = discord_common.embed_success(f"Updated handles for server members.")
        await ctx.send(embed=embed)

    def _set_update(self, ctx, member, user):
        handle = user.handle
        try:
            cf_common.user_db.set_handle(member.id, ctx.guild.id, handle)
        except db.UniqueConstraintFailed:
            raise HandleCogError(
                f"The handle `{handle}` is already associated with another user."
            )
        cf_common.user_db.cache_cf_user(user)

    async def _set_with_role(
        self, ctx, member, user, update_reason="New handle set for user"
    ):
        if user.rank == cf.UNRATED_RANK:
            role_title = "Newbie"
        else:
            role_title = user.rank.title

        roles = [role for role in ctx.guild.roles if role.name == role_title]
        if not roles:
            raise HandleCogError(
                f"Role for rank `{user.rank.title}` not present in the server"
            )
        role_to_assign = roles[0]

        await self.update_member_rank_role(member, role_to_assign, reason=update_reason)
        embed = _make_profile_embed(member, user, mode="set")
        await ctx.send(embed=embed)

    async def _set(self, ctx, member, user):
        self._set_update(ctx, member, user)
        await self._set_with_role(ctx, member, user)

    @handle.command(brief="Identify yourself", usage="[handle]")
    @cf_common.user_guard(
        group="handle",
        get_exception=lambda: HandleCogError(
            "Identification is already running for you"
        ),
    )
    async def identify(self, ctx, handle: str):
        """Link a codeforces account to discord account by submitting a compile error to a random problem"""
        if cf_common.user_db.get_handle(ctx.author.id, ctx.guild.id):
            users = await cf.user.info(handles=[handle])
            await self._set_with_role(ctx, ctx.author, users[0], "User reidentified")
            return

        if cf_common.user_db.get_user_id(handle, ctx.guild.id):
            raise HandleCogError(
                f"The handle `{handle}` is already associated with another user. Ask an Admin in case of an inconsistency."
            )

        if handle in cf_common.HandleIsVjudgeError.HANDLES:
            raise cf_common.HandleIsVjudgeError(handle)

        users = await cf.user.info(handles=[handle])
        invoker = str(ctx.author)
        user_invoker = users[0]
        handle = user_invoker.handle
        org = user_invoker.organization

        if org != "IIIT Hyderabad":
            raise HandleCogError(
                f"{ctx.author.mention}, please visit https://codeforces.com/settings/social"
                " and set your organization to 'IIIT Hyderabad' (without quotes). Only then"
                " your handle will be identified. If you're an alumni, contact `@bot-admin`."
            )

        problems = [
            prob
            for prob in cf_common.cache2.problem_cache.problems
            if prob.rating <= 1200
        ]
        problem = random.choice(problems)
        await ctx.send(
            f"`{invoker}`, submit a compile error to <{problem.url}> within 60 seconds"
        )
        await asyncio.sleep(60)

        subs = await cf.user.status(handle=handle, count=5)
        if any(
            sub.problem.name == problem.name and sub.verdict == "COMPILATION_ERROR"
            for sub in subs
        ):
            users = await cf.user.info(handles=[handle])
            await self._set(ctx, ctx.author, users[0])
        else:
            await ctx.send(f"Sorry `{invoker}`, can you try again?")

    @handle.command(brief="Get handle by Discord username")
    async def get(self, ctx, member: discord.Member):
        """Show Codeforces handle of a user."""
        handle = cf_common.user_db.get_handle(member.id, ctx.guild.id)
        if not handle:
            raise HandleCogError(f"Handle for {member.mention} not found in database")
        user = cf_common.user_db.fetch_cf_user(handle)
        embed = _make_profile_embed(member, user, mode="get")
        await ctx.send(embed=embed)

    @handle.command(brief="Get Discord username by cf handle")
    async def rget(self, ctx, handle: str):
        """Show Discord username of a cf handle."""
        user_id = cf_common.user_db.get_user_id(handle, ctx.guild.id)
        if not user_id:
            raise HandleCogError(
                f"Discord username for `{handle}` not found in database"
            )
        user = cf_common.user_db.fetch_cf_user(handle)
        member = ctx.guild.get_member(int(user_id))
        embed = _make_profile_embed(member, user, mode="get")
        await ctx.send(embed=embed)

    @handle.command(brief="Remove handle for a user")
    @commands.has_any_role("Admin", "Moderator")
    async def remove(self, ctx, member: discord.Member):
        """Remove Codeforces handle of a user."""
        rc = cf_common.user_db.remove_handle(member.id, ctx.guild.id)
        if not rc:
            raise HandleCogError(f"Handle for {member.mention} not found in database")
        await self.update_member_rank_role(
            member, role_to_assign=None, reason="Handle removed for user"
        )
        embed = discord_common.embed_success(f"Removed handle for {member.mention}")
        await ctx.send(embed=embed)

    @commands.command(brief="Show gudgitters", aliases=["gitgudders"])
    async def gudgitters(self, ctx):
        """Show the list of users of gitgud with their scores."""
        res = cf_common.user_db.get_gudgitters()
        res.sort(key=lambda r: r[1], reverse=True)

        rankings = []
        index = 0
        for user_id, score in res:
            member = ctx.guild.get_member(int(user_id))
            if member is None:
                continue
            if score > 0:
                handle = cf_common.user_db.get_handle(user_id, ctx.guild.id)
                user = cf_common.user_db.fetch_cf_user(handle)
                handle_display = f"{member.display_name} ({score})"
                rating = user.rating if user.rating is not None else "Unrated"
                rankings.append((index, handle_display, handle, rating))
                index += 1
            if index == 10:
                break

        if not rankings:
            raise HandleCogError(
                "No one has completed a gitgud challenge, send ;gitgud to request and ;gotgud to mark it as complete"
            )
        discord_file = get_gudgitters_image(rankings)
        await ctx.send(file=discord_file)

    @handle.command(brief="Show all handles")
    async def list(self, ctx, *countries):
        """Shows members of the server who have registered their handles and
        their Codeforces ratings. You can additionally specify a list of countries
        if you wish to display only members from those countries. Country data is
        sourced from codeforces profiles. e.g. ;handle list Croatia Slovenia
        """
        countries = [country.title() for country in countries]
        res = cf_common.user_db.get_cf_users_for_guild(ctx.guild.id)
        users = [
            (ctx.guild.get_member(user_id), cf_user.handle, cf_user.rating)
            for user_id, cf_user in res
            if not countries or cf_user.country in countries
        ]
        users = [
            (member, handle, rating)
            for member, handle, rating in users
            if member is not None
        ]
        if not users:
            raise HandleCogError("No members with registered handles.")

        # Sorting by (-rating, handle)
        users.sort(key=lambda x: (1 if x[2] is None else -x[2], x[1]))
        title = "Handles of server members"
        if countries:
            title += " from " + ", ".join(f"`{country}`" for country in countries)
        pages = _make_pages(users, title)
        paginator.paginate(
            self.bot,
            ctx.channel,
            pages,
            wait_time=_PAGINATE_WAIT_TIME,
            set_pagenum_footers=True,
        )

    @handle.command(brief="Show handles, but prettier")
    async def pretty(self, ctx, page_no: int = None):
        """Show members of the server who have registered their handles and their Codeforces
        ratings, in color.
        """
        user_id_cf_user_pairs = cf_common.user_db.get_cf_users_for_guild(ctx.guild.id)
        user_id_cf_user_pairs.sort(
            key=lambda p: p[1].rating if p[1].rating is not None else -1,
            reverse=True,
        )
        rows = []
        author_idx = None
        for user_id, cf_user in user_id_cf_user_pairs:
            member = ctx.guild.get_member(user_id)
            if member is None:
                continue
            idx = len(rows)
            if member == ctx.author:
                author_idx = idx
            rows.append((idx, member.display_name, cf_user.handle, cf_user.rating))

        if not rows:
            raise HandleCogError("No members with registered handles.")
        max_page = math.ceil(len(rows) / _PRETTY_HANDLES_PER_PAGE) - 1
        if author_idx is None and page_no is None:
            raise HandleCogError(
                f"Please specify a page number between 0 and {max_page}."
            )

        msg = None
        if page_no is not None:
            if page_no < 0 or max_page < page_no:
                msg_fmt = "Page number must be between 0 and {}. Showing page {}."
                if page_no < 0:
                    msg = msg_fmt.format(max_page, 0)
                    page_no = 0
                else:
                    msg = msg_fmt.format(max_page, max_page)
                    page_no = max_page
            start_idx = page_no * _PRETTY_HANDLES_PER_PAGE
        else:
            msg = f"Showing neighbourhood of user `{ctx.author.display_name}`."
            num_before = (_PRETTY_HANDLES_PER_PAGE - 1) // 2
            start_idx = max(0, author_idx - num_before)
        rows_to_display = rows[start_idx : start_idx + _PRETTY_HANDLES_PER_PAGE]
        img = get_prettyhandles_image(rows_to_display, self.font)
        buffer = io.BytesIO()
        img.save(buffer, "png")
        buffer.seek(0)
        await ctx.send(msg, file=discord.File(buffer, "handles.png"))

    async def _update_ranks(self, guild):
        """For each member in the guild, fetches their current ratings and updates their role if
        required.
        """
        res = cf_common.user_db.get_handles_for_guild(guild.id)
        member_handles = [
            (guild.get_member(int(user_id)), handle) for user_id, handle in res
        ]
        member_handles = [
            (member, handle) for member, handle in member_handles if member is not None
        ]
        if not member_handles:
            raise HandleCogError("Handles not set for any user")
        members, handles = zip(*member_handles)
        users = await cf.user.info(handles=handles)
        for user in users:
            cf_common.user_db.cache_cf_user(user)

        required_roles = {
            user.rank.title for user in users if user.rank != cf.UNRATED_RANK
        }
        rank2role = {
            role.name: role for role in guild.roles if role.name in required_roles
        }
        missing_roles = required_roles - rank2role.keys()
        if missing_roles:
            roles_str = ", ".join(f"`{role}`" for role in missing_roles)
            plural = "s" if len(missing_roles) > 1 else ""
            raise HandleCogError(
                f"Role{plural} for rank{plural} {roles_str} not present in the server"
            )

        for member, user in zip(members, users):
            role_to_assign = (
                None if user.rank == cf.UNRATED_RANK else rank2role[user.rank.title]
            )
            await self.update_member_rank_role(
                member, role_to_assign, reason="Codeforces rank update"
            )

    @staticmethod
    def _make_rankup_embed(guild, contest, change_by_handle, ranklist):
        """Make an embed containing a list of rank changes and top ranks (official/unofficial)
        for the members of this guild.
        """
        user_id_handle_pairs = cf_common.user_db.get_handles_for_guild(guild.id)
        member_handle_pairs = [
            (guild.get_member(int(user_id)), handle)
            for user_id, handle in user_id_handle_pairs
        ]
        handle_to_member = {
            handle: member
            for member, handle in member_handle_pairs
            if member is not None
        }

        member_change_pairs = [
            (member, change_by_handle[handle])
            for member, handle in member_handle_pairs
            if member is not None and handle in change_by_handle
        ]

        # We can also display unofficial top 5 ranks
        # even if member_change_pairs is empty

        member_change_pairs.sort(key=lambda pair: pair[1].newRating, reverse=True)
        rank_to_role = {role.name: role for role in guild.roles}

        def rating_to_displayable_rank(rating):
            rank = cf.rating2rank(rating).title
            role = rank_to_role.get(rank)
            return role.mention if role else rank

        rank_changes_str = []
        for member, change in member_change_pairs:
            cache = cf_common.cache2.rating_changes_cache
            if (
                change.oldRating == 1500
                and len(cache.get_rating_changes_for_handle(change.handle)) == 1
            ):
                # If this is the user's first rated contest.
                old_role = "Unrated"
            else:
                old_role = rating_to_displayable_rank(change.oldRating)
            new_role = rating_to_displayable_rank(change.newRating)
            if new_role != old_role:
                rank_change_str = (
                    f"{member.mention} [{change.handle}]({cf.PROFILE_BASE_URL}{change.handle}): {old_role} "
                    f"\N{LONG RIGHTWARDS ARROW} {new_role}"
                )
                rank_changes_str.append(rank_change_str)

        participant_handles = [
            (
                rankrow.party.members[0].handle,
                rankrow.party.participantType,
                rankrow.rank,
            )
            for rankrow in ranklist.standings
        ]

        participant_member_handles = [
            (handle_to_member[handle], handle, rank, part_type == "CONTESTANT")
            for handle, part_type, rank in participant_handles
            if (part_type == "CONTESTANT" or part_type == "OUT_OF_COMPETITION")
            and handle in handle_to_member
        ]

        member_change_pairs.sort(
            key=lambda pair: pair[1].newRating - pair[1].oldRating, reverse=True
        )
        top_increases_str = []
        for member, change in member_change_pairs[:_TOP_DELTAS_COUNT]:
            delta = change.newRating - change.oldRating
            if delta <= 0:
                break
            increase_str = (
                f"{member.mention} [{change.handle}]({cf.PROFILE_BASE_URL}{change.handle}): {change.oldRating} "
                f"\N{HORIZONTAL BAR} **{delta:+}** \N{LONG RIGHTWARDS ARROW} "
                f"{change.newRating}"
            )
            top_increases_str.append(increase_str)

        top_ranks_str = []
        top_ranks_str_official = []
        for member, handle, rank, is_official in participant_member_handles:
            mention_str = f"{member.mention} [{handle}]({cf.PROFILE_BASE_URL}{handle}): rank {rank}"

            if len(top_ranks_str) < _MAX_TOP_RANKS:
                top_ranks_str.append(mention_str)
            if len(top_ranks_str_official) < _MAX_TOP_RANKS and is_official:
                top_ranks_str_official.append(mention_str)

        # Could be written with a one-liner with or
        if not rank_changes_str:
            rank_changes_str = ["No rank changes"]

        # Reconsider this approach, see notes in cf_common
        embeds = []
        embed_color_seed = random.randrange(0, 100)

        """
        Use paginator.chunkify to make this readable. The changes suggested below would remove 
        the need to have start to recognize the first iteration and should make things cleaner 
        in general. This might simplify to the degree that you could use a list comprehension.
        """
        for start in range(0, len(rank_changes_str), _MAX_RATING_CHANGES_PER_EMBED):
            desc = "\n".join(
                rank_changes_str[start : start + _MAX_RATING_CHANGES_PER_EMBED]
            )

            """
            Can fields be set afterwards in general? If so consider generating the more generic 
            version (simpler and more obvious code in the loop) and set the relevant stuff for 
            embed[0] afterwards. This also removes the need for the awkward usage of start.
            """
            if start:
                embed = discord_common.cf_color_embed_fixed(
                    description=desc, seed=embed_color_seed
                )
            else:
                embed = discord_common.cf_color_embed_fixed(
                    title=contest.name,
                    url=contest.url,
                    description=desc,
                    seed=embed_color_seed,
                )
                embed.set_author(name="Rank updates")

            """
            Some general issues with whitespace and long lines, could you run a formatter 
            (e.g. yapf) on your changes?
            """
            embeds.append(embed)

        top_rating_increases_embed = discord_common.cf_color_embed_fixed(
            description="\n".join(top_increases_str)
            or "Nobody got a positive delta :(",
            seed=embed_color_seed,
        )
        top_rating_increases_embed.set_author(name="Top rating increases")
        embeds.append(top_rating_increases_embed)

        def make_best_ranks_embed(official):
            top_ranks_list = top_ranks_str_official if official else top_ranks_str
            emoji = ":partying_face:" if official else ":raised_hands:"

            if official and top_ranks_str_official == top_ranks_str:
                return None

            description = (
                "\n".join(top_ranks_list)
                or "Nobody participated in todays contest :("
            )

            title = "Top five ranks "
            if top_ranks_str == top_ranks_str_official:
                title += "(all)"
            else:
                title += "(official)" if official else "(all)"
            title_use = f"{title} {emoji}"

            # make the embed object
            embed_obj = discord_common.cf_color_embed_fixed(
                description=description,
                title=title_use,
                seed=embed_color_seed,
            )
            cong_strings = [
                "Join me in congratulating...",
                "Let us congratulate...",
                "Welcoming our warriors...",
                "Rocking today's contest are...",
                "Stupendous performance today by...",
            ]

            cong_string = random.choice(cong_strings)
            embed_obj.set_author(name=cong_string)
            return embed_obj

        top_five_ranks_official = make_best_ranks_embed(official=True)
        top_five_ranks_unofficial = make_best_ranks_embed(official=False)

        if top_five_ranks_official:
            embeds.append(top_five_ranks_official)
        if top_five_ranks_unofficial:
            embeds.append(top_five_ranks_unofficial)

        return embeds

    @commands.group(brief="Commands for role updates", invoke_without_command=True)
    async def roleupdate(self, ctx):
        """Group for commands involving role updates."""
        await ctx.send_help(ctx.command)

    @roleupdate.command(brief="Update Codeforces rank roles")
    @commands.has_any_role("Admin", "Moderator")
    async def now(self, ctx):
        """Updates Codeforces rank roles for every member in this server."""
        await self._update_ranks(ctx.guild)
        await ctx.send(
            embed=discord_common.embed_success("Roles updated successfully.")
        )

    @roleupdate.command(brief="Enable or disable auto role updates", usage="on|off")
    @commands.has_any_role("Admin", "Moderator")
    async def auto(self, ctx, arg):
        """Auto role update refers to automatic updating of rank roles when rating
        changes are released on Codeforces. 'on'/'off' disables or enables auto role
        updates.
        """
        if arg == "on":
            rc = cf_common.user_db.enable_auto_role_update(ctx.guild.id)
            if not rc:
                raise HandleCogError("Auto role update is already enabled.")
            await ctx.send(
                embed=discord_common.embed_success("Auto role updates enabled.")
            )
        elif arg == "off":
            rc = cf_common.user_db.disable_auto_role_update(ctx.guild.id)
            if not rc:
                raise HandleCogError("Auto role update is already disabled.")
            await ctx.send(
                embed=discord_common.embed_success("Auto role updates disabled.")
            )
        else:
            raise ValueError(f"arg must be 'on' or 'off', got '{arg}' instead.")

    @roleupdate.command(
        brief="Publish a rank update for the given contest",
        usage="here|off|contest_id",
    )
    @commands.has_any_role("Admin", "Moderator")
    async def publish(self, ctx, arg):
        """This is a feature to publish a summary of rank changes and top rating
        increases in a particular contest for members of this server. 'here' will
        automatically publish the summary to this channel whenever rating changes on
        Codeforces are released. 'off' will disable auto publishing. Specifying a
        contest id will publish the summary immediately.
        """
        if arg == "here":
            cf_common.user_db.set_rankup_channel(ctx.guild.id, ctx.channel.id)
            await ctx.send(
                embed=discord_common.embed_success(
                    "Auto rank update publishing enabled."
                )
            )
        elif arg == "off":
            rc = cf_common.user_db.clear_rankup_channel(ctx.guild.id)
            if not rc:
                raise HandleCogError("Rank update publishing is already disabled.")
            await ctx.send(
                embed=discord_common.embed_success("Rank update publishing disabled.")
            )
        else:
            try:
                contest_id = int(arg)
            except ValueError:
                raise ValueError(
                    f"arg must be 'here', 'off' or a contest ID, got '{arg}' instead."
                )
            await self._publish_now(ctx, contest_id)

    async def _publish_now(self, ctx, contest_id):
        try:
            contest = cf_common.cache2.contest_cache.get_contest(contest_id)
        except cache_system2.ContestNotFound as e:
            raise HandleCogError(f"Contest with id `{e.contest_id}` not found.")
        if contest.phase != "FINISHED":
            raise HandleCogError(
                f"Contest `{contest_id} | {contest.name}` has not finished."
            )
        try:
            changes = await cf.contest.ratingChanges(contest_id=contest_id)
        except cf.RatingChangesUnavailableError:
            changes = None
        if not changes:
            raise HandleCogError(
                f"Rating changes are not available for contest `{contest_id} | "
                f"{contest.name}`."
            )

        try:
            ranklist = cf_common.cache2.ranklist_cache.get_ranklist(contest)
        except cache_system2.RanklistNotMonitored:
            ranklist = await cf_common.cache2.ranklist_cache.generate_ranklist(
                contest.id, fetch_changes=True
            )

        change_by_handle = {change.handle: change for change in changes}
        rankup_embeds = self._make_rankup_embed(
            ctx.guild, contest, change_by_handle, ranklist
        )
        for rankup_embed in rankup_embeds:
            await ctx.channel.send(embed=rankup_embed)

    async def _generic_remind(self, ctx, action, role_name, what):
        roles = [role for role in ctx.guild.roles if role.name == role_name]
        if not roles:
            raise HandleCogError(f"Role `{role_name}` not present in the server")
        role = roles[0]
        if action == "give":
            if role in ctx.author.roles:
                await ctx.send(
                    embed=discord_common.embed_neutral(
                        f"You are already subscribed to {what} reminders"
                    )
                )
                return
            await ctx.author.add_roles(
                role, reason=f"User subscribed to {what} reminders"
            )
            await ctx.send(
                embed=discord_common.embed_success(
                    f"Successfully subscribed to {what} reminders"
                )
            )
        elif action == "remove":
            if role not in ctx.author.roles:
                await ctx.send(
                    embed=discord_common.embed_neutral(
                        f"You are not subscribed to {what} reminders"
                    )
                )
                return
            await ctx.author.remove_roles(
                role, reason=f"User unsubscribed from {what} reminders"
            )
            await ctx.send(
                embed=discord_common.embed_success(
                    f"Successfully unsubscribed from {what} reminders"
                )
            )
        else:
            raise HandleCogError(f"Invalid action {action}")

    @commands.command(
        brief="Grants or removes the specified pingable role",
        usage="[give/remove] [vc/duel]",
    )
    async def role(self, ctx, action: str, which: str):
        """e.g. ;role remove duel"""
        if which == "vc":
            await self._generic_remind(ctx, action, "Virtual Contestant", "vc")
        elif which == "duel" or which == "dual":
            await self._generic_remind(ctx, action, "Duelist", "duel")
        else:
            raise HandleCogError(f"Invalid role {which}")

    @discord_common.send_error_if(HandleCogError, cf_common.HandleIsVjudgeError)
    async def cog_command_error(self, ctx, error):
        pass


def setup(bot):
    bot.add_cog(Handles(bot))
