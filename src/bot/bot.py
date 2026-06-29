"""Bot de Telegram de suscripción (python-telegram-bot v20+, async).

Comandos:
  /start     bienvenida + verificación de edad (+18) + disclaimer
  /subscribe activar / renovar la suscripción (cobro desacoplado, stub manual)
  /status    estado de la suscripción
  /today     recomendaciones del día (SOLO suscriptores activos)
  /record    track record público (desde la capa de reporting/publicaciones)
  /help      ayuda
  /grant     (admin) confirma pago manual y activa a un usuario

El gating se aplica en `/today` y demás contenido premium: requiere edad
verificada (+18) y suscripción activa. La matemática vive en `BettingService`;
aquí solo se entrega y se controla el acceso.
"""
from __future__ import annotations

from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from config import CONFIG, Config
from src.bot import content, texts
from src.service import BettingService
from src.subscriptions.payments import ManualPaymentProvider
from src.subscriptions.service import SubscriptionService


class BettingBot:
    """Encapsula la Application de Telegram y sus dependencias."""

    def __init__(
        self,
        config: Config = CONFIG,
        *,
        service: Optional[BettingService] = None,
        subscriptions: Optional[SubscriptionService] = None,
    ) -> None:
        self.config = config
        self.service = service or BettingService(config)
        self.subscriptions = subscriptions or SubscriptionService(config)

    # ───────────────────────── construcción ─────────────────────────

    def build_application(self, *, with_scheduler: bool = False) -> Application:
        if not self.config.telegram_bot_token:
            raise RuntimeError(
                "Falta TELEGRAM_BOT_TOKEN. Añádelo a .env (token de BotFather)."
            )
        builder = ApplicationBuilder().token(self.config.telegram_bot_token)
        if with_scheduler:
            builder = builder.post_init(self._start_scheduler)
        app = builder.build()
        app.add_handler(CommandHandler("start", self.cmd_start))
        app.add_handler(CommandHandler("help", self.cmd_help))
        app.add_handler(CommandHandler("subscribe", self.cmd_subscribe))
        app.add_handler(CommandHandler("status", self.cmd_status))
        app.add_handler(CommandHandler("today", self.cmd_today))
        app.add_handler(CommandHandler("record", self.cmd_record))
        app.add_handler(CommandHandler("grant", self.cmd_grant))
        app.add_handler(CallbackQueryHandler(self.on_age_callback, pattern=r"^age:"))
        # Guarda referencia para el scheduler.
        app.bot_data["bot_facade"] = self
        return app

    # ───────────────────────────── helpers ─────────────────────────────

    @staticmethod
    def _uid(update: Update) -> int:
        return update.effective_user.id

    async def _reply(self, update: Update, text: str) -> None:
        await update.effective_message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

    # ───────────────────────────── handlers ─────────────────────────────

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        uid = self._uid(update)
        self.subscriptions.register(uid)
        if self.subscriptions.is_age_verified(uid):
            await self._reply(update, texts.WELCOME + "\n\n" + texts.DISCLAIMER_COMPLETO)
            return
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Sí, soy +18", callback_data="age:yes"),
            InlineKeyboardButton("🚫 No", callback_data="age:no"),
        ]])
        await update.effective_message.reply_text(
            texts.WELCOME + "\n\n" + texts.AGE_PROMPT,
            parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard,
        )

    async def on_age_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        await query.answer()
        uid = query.from_user.id
        if query.data == "age:yes":
            self.subscriptions.verify_age(uid, confirmed_18=True)
            await query.edit_message_text(texts.AGE_CONFIRMED, parse_mode=ParseMode.MARKDOWN)
        else:
            self.subscriptions.verify_age(uid, confirmed_18=False)
            await query.edit_message_text(texts.AGE_DENIED, parse_mode=ParseMode.MARKDOWN)

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._reply(update, texts.HELP)

    async def cmd_subscribe(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        uid = self._uid(update)
        if not self.subscriptions.is_age_verified(uid):
            await self._reply(update, texts.NEED_AGE)
            return
        try:
            checkout = self.subscriptions.start_checkout(uid)
        except PermissionError:
            await self._reply(update, texts.NEED_AGE)
            return
        # Intenta activar si el pago ya está confirmado (p.ej. admin lo marcó).
        try:
            user = self.subscriptions.activate(uid)
            await self._reply(
                update,
                f"✅ Suscripción *{user.tier}* activa. Días restantes: "
                f"{user.dias_restantes()}.\nUsa /today para tus recomendaciones.",
            )
        except RuntimeError:
            await self._reply(
                update,
                "💳 *Activar suscripción*\n" + checkout.instructions + "\n\n"
                + texts.DISCLAIMER_CORTO,
            )

    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        uid = self._uid(update)
        user = self.subscriptions.status(uid)
        edad = "sí" if user.edad_verificada else "no"
        if user.is_subscription_active():
            txt = (
                f"🟢 *Suscripción activa*\nTier: {user.tier}\n"
                f"Días restantes: {user.dias_restantes()}\nEdad verificada: {edad}"
            )
        else:
            txt = (
                f"🔴 *Sin suscripción activa* (estado: {user.estado})\n"
                f"Edad verificada: {edad}\nUsa /subscribe para activarla."
            )
        await self._reply(update, txt)

    async def cmd_today(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        uid = self._uid(update)
        if not self.subscriptions.is_age_verified(uid):
            await self._reply(update, texts.NEED_AGE)
            return
        if not self.subscriptions.is_entitled(uid):
            await self._reply(update, texts.NEED_SUBSCRIPTION)
            return
        # Argumento opcional: fecha YYYY-MM-DD.
        date_iso = context.args[0] if context.args else None
        try:
            recs = content.build_today(
                self.service, date_iso=date_iso,
                max_recs=self.config.max_recs_publicadas,
            )
            jornada = date_iso or "hoy"
            await self._reply(update, content.format_today_text(recs, jornada=jornada))
        except Exception as exc:  # noqa: BLE001
            await self._reply(update, f"⚠️ No pude generar recomendaciones ahora: {exc}")

    async def cmd_record(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            txt = content.record_text(self.subscriptions.store, self.service)
        except Exception as exc:  # noqa: BLE001
            txt = f"⚠️ No pude generar el track record: {exc}"
        await self._reply(update, txt)

    async def cmd_grant(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Admin: confirma pago manual y activa a un usuario. /grant <telegram_id>."""
        uid = self._uid(update)
        if not self.config.admin_telegram_id or uid != self.config.admin_telegram_id:
            await self._reply(update, "Comando solo para el admin.")
            return
        if not context.args:
            await self._reply(update, "Uso: /grant <telegram_id>")
            return
        try:
            target = int(context.args[0])
        except ValueError:
            await self._reply(update, "ID inválido.")
            return
        provider = self.subscriptions.payments
        if isinstance(provider, ManualPaymentProvider):
            provider.mark_paid(target)
        try:
            user = self.subscriptions.activate(target, skip_age_check=True)
            await self._reply(
                update,
                f"✅ Activado {target}: tier {user.tier}, {user.dias_restantes()} días.",
            )
        except Exception as exc:  # noqa: BLE001
            await self._reply(update, f"No pude activar: {exc}")

    async def _start_scheduler(self, app: Application) -> None:
        """post_init: arranca el scheduler en background con un broadcast que
        envía por Telegram en el event loop del bot."""
        import asyncio

        from src.scheduler.jobs import build_scheduler

        loop = asyncio.get_running_loop()

        def broadcast(text: str, chat_ids: list[int]) -> None:
            for cid in chat_ids:
                asyncio.run_coroutine_threadsafe(
                    app.bot.send_message(cid, text, parse_mode=ParseMode.MARKDOWN), loop
                )

        self._scheduler = build_scheduler(
            self.service, self.subscriptions, broadcast, config=self.config
        )
        self._scheduler.start()

    def run_polling(self, *, with_scheduler: bool = False) -> None:
        app = self.build_application(with_scheduler=with_scheduler)
        app.run_polling()
