import logging
import asyncio
import discord
from datetime import datetime

from redbot.core import checks, commands, Config, modlog
from redbot.core.utils.chat_formatting import pagify
from redbot.core.utils.predicates import MessagePredicate
from typing import Literal

try:
    from redbot.core.commands import GuildContext
except ImportError:
    from redbot.core.commands import Context as GuildContext

__author__ = "TheBluekr#2702"
__cogname__ = "aurelia.cogs.roletracker"

# 0 is reason, 1 is role object, 2 is extra msg
REASON_MSG = "{0}\n**Role:**{1.mention} (name: {1.name}, id: {1.id})\n{2}"


class RoleTracker(commands.Cog):
    def __init__(self, bot):
        super().__init__()
        self.bot = bot
        self.logger = logging.getLogger(__cogname__)
        self.config = Config.get_conf(self, identifier=428038701, force_registration=True)

        default_role = {"addable": False, "USERS": {}}

        self.config.register_role(**default_role)

    async def initialize(self):
        await self.register_casetypes()

    @staticmethod
    async def register_casetypes():
        # register mod case
        role_case = {
            "name": "roleupdate",
            "default_setting": True,
            "image": "\N{PAGE FACING UP}",
            "case_str": "Role Update",
        }
        try:
            await modlog.register_casetype(**role_case)
        except RuntimeError:
            pass

    # Commands
    @commands.group(aliases=["rtrack", "roletrack"])
    @commands.guild_only()
    @checks.mod_or_permissions(manage_roles=True)
    async def roletracker(self, ctx: GuildContext):
        """Role Tracker commands"""
        pass

    @roletracker.command(name="set")
    @checks.admin_or_permissions(administrator=True)
    async def set_role(self, ctx: GuildContext, role: discord.Role, enabled: bool):
        """
        Sets role to be addable/removable
        """
        if not enabled:
            if await self.config.role(role).USERS():
                pred = MessagePredicate.yes_or_no(ctx)
                await ctx.maybe_send_embed(f"Found logs for role {role}, do you want to erase them?")
                try:
                    await self.bot.wait_for("message", check=pred, timeout=30)
                except asyncio.TimeoutError:
                    return await ctx.send("Timed out.")
                if pred.result:
                    await self.config.role(role).USERS.set({})

            await self.config.role(role).addable.set(False)
            await ctx.tick()
        else:
            await self.config.role(role).addable.set(True)
            await ctx.tick()

    @roletracker.command(name="roles")
    async def view_roles(self, ctx: GuildContext):
        """
        Lists all roles which can be added/removed
        """
        enabled = list()
        for role in ctx.guild.roles:
            if await self.config.role(role).addable() and not role.is_default():
                enabled.append(role.name)

        pages = pagify("\n".join(enabled))

        await ctx.send("List of addable roles:")
        for page in pages:
            await ctx.maybe_send_embed(page)

    @commands.bot_has_permissions(manage_roles=True)
    @roletracker.command(name="add")
    async def add_role(
        self, ctx: GuildContext, role: discord.Role, member: discord.Member, *, reason: str = f"Added by {__cogname__}"
    ):
        """
        Adds specified role to given user
        """
        if not await self.config.role(role).addable():
            return await ctx.maybe_send_embed("Role isn't set as addable.")
        try:
            if role in member.roles:
                return await ctx.maybe_send_embed("Member already has that role.")

            data = await self.config.role(role).USERS()
            if len(ctx.message.attachments):
                attachment = ctx.message.attachments[0]
                reason_message = REASON_MSG.format(reason, role, attachment.url)
            else:
                await ctx.send(f"Couldn't find attachment, do you want to continue without adding attachment?")
                pred = MessagePredicate.yes_or_no(ctx)

                try:
                    await self.bot.wait_for("message", check=pred, timeout=30)
                except asyncio.TimeoutError:
                    return await ctx.send("Timed out.")

                if pred.result:
                    reason_message = REASON_MSG.format(reason, role, "No attachment.")
                else:
                    return await ctx.maybe_send_embed("Cancelling command.")

            case = await modlog.create_case(
                self.bot,
                member.guild,
                ctx.message.created_at,
                "roleupdate",
                member,
                moderator=ctx.author,
                reason=reason_message,
            )

            caseno = case.case_number
            data[member.id] = caseno
            await self.config.role(role).USERS.set(data)

            await member.add_roles(role)
            await ctx.tick()
        except discord.Forbidden:
            return await ctx.maybe_send_embed("Can't do that. Discord role heirarchy applies here.")

    @commands.bot_has_permissions(manage_roles=True)
    @roletracker.command(name="remove")
    async def remove_role(
        self,
        ctx: GuildContext,
        role: discord.Role,
        member: discord.Member,
        *,
        reason: str = f"Removed by {__cogname__}.",
    ):
        """
        Removes specified role from given user
        """

        if not await self.config.role(role).addable():
            return await ctx.maybe_send_embed("Role isn't set as removable.")

        guild = ctx.guild

        try:
            if role not in member.roles:
                return await ctx.maybe_send_embed("Member doesn't have that role.")

            data = await self.config.role(role).USERS()
            caseno = data.pop(str(member.id), None)

            if caseno:
                try:
                    case = await modlog.get_case(caseno, guild, self.bot)
                except RuntimeError:
                    self.logger.error(f"Failed to find case for {member}, case number: {case}")
                    case = None

                if case:
                    edits = {"reason": case.reason + f"\n**Role Removed**: {reason}"}

                    if ctx.message.author.id != case.moderator.id:
                        edits["amended_by"] = ctx.message.author

                    edits["modified_at"] = ctx.message.created_at.timestamp()

                    await case.edit(edits)

            await self.config.role(role).USERS.set(data)
            await member.remove_roles(role)
            await ctx.tick()
        except discord.Forbidden:
            return await ctx.maybe_send_embed("Can't do that. Discord role heirarchy applies here.")

    # Listeners
    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        """
        Listens for role updates and more
        """
        if await self.bot.cog_disabled_in_guild(self, after.guild):
            return

        if before.roles != after.roles:
            user = self.bot.user
            try:
                async for entry in after.guild.audit_logs(limit=3, action=discord.AuditLogAction.member_role_update):
                    if entry.target.id == before.id:
                        user = entry.user
                        break

            except discord.Forbidden:
                self.logger.warning("Failed to retrieve the moderator from audit logs, please check the permissions")

            broles = set(before.roles)
            aroles = set(after.roles)
            added = aroles - broles
            removed = broles - aroles

            now_date = datetime.utcnow()

            for role in added:
                role_dict = await self.config.role(role).all()
                if role_dict["addable"] and not (
                    role_dict["USERS"].get(str(before.id), None) or user == before.guild.me
                ):
                    data = role_dict["USERS"]

                    case = await modlog.create_case(
                        self.bot,
                        before.guild,
                        now_date,
                        "roleupdate",
                        before,
                        moderator=user,
                        reason=REASON_MSG.format("Role manually added", role, ""),
                    )
                    caseno = case.case_number

                    data[before.id] = caseno
                    await self.config.role(role).USERS.set(data)

            for role in removed:
                role_dict = await self.config.role(role).all()
                if role_dict["addable"] and not user == before.guild.me:
                    data = role_dict["USERS"]

                    caseno = data.pop(str(before.id), None)

                    if caseno:
                        try:
                            case = await modlog.get_case(caseno, before.guild, self.bot)
                        except RuntimeError:
                            self.logger.error(f"Failed to find case for {before.user}, case number: {case}")
                            case = None

                        if case:
                            edits = {"reason": case.reason + f"\n**Role Removed**: Role manually removed"}

                            if user.id != case.moderator.id:
                                edits["amended_by"] = user

                            edits["modified_at"] = now_date.timestamp()

                            await case.edit(edits)

                    await self.config.role(role).USERS.set(data)

    async def red_delete_data_for_user(
        self,
        *,
        requester: Literal["discord_deleted_user", "owner", "user", "user_strict"],
        user_id: int,
    ):
        pass
