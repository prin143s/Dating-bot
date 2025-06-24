import os
from fastapi import FastAPI, Request
from pymongo import MongoClient
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)

# Environment variables
BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGODB_URI = os.getenv("MONGODB_URI")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # https://your-app-name.onrender.com
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME")  # without @

# MongoDB setup
client = MongoClient(MONGODB_URI)
db = client["datingbot"]
users_col = db["users"]
likes_col = db["likes"]
chats_col = db["chats"]

# Telegram & FastAPI setup
telegram_app = Application.builder().token(BOT_TOKEN).updater(None).build()

app = FastAPI()

# Start command with force join check
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id

    # ✅ Check channel membership
    try:
        member = await context.bot.get_chat_member(f"@{CHANNEL_USERNAME}", uid)
        if member.status in ("left", "kicked"):
            raise Exception("Not in channel")
    except:
        btn = InlineKeyboardMarkup([[
            InlineKeyboardButton("📢 Join Channel", url=f"https://t.me/{CHANNEL_USERNAME}")
        ]])
        await update.message.reply_text("🚫 Join our channel to use the bot.", reply_markup=btn)
        return

    users_col.update_one({"_id": uid}, {"$set": {"step": "name"}}, upsert=True)
    await update.message.reply_text("👋 Welcome to the Dating Bot!Created by @Prince_x_010\nWhat's your name?")

# Handle profile creation and chat
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text

    # If in a matched chat, forward messages
    chat = chats_col.find_one({"$or": [{"user1": uid}, {"user2": uid}]})
    if chat:
        partner_id = chat["user2"] if chat["user1"] == uid else chat["user1"]
        await context.bot.send_message(chat_id=partner_id, text=f"{update.effective_user.first_name}: {text}")
        chats_col.update_one({"_id": chat["_id"]}, {"$push": {"messages": {"from": uid, "text": text}}})
        return

    user = users_col.find_one({"_id": uid})
    if not user:
        return await update.message.reply_text("Type /start to begin.")

    step = user.get("step")
    update_fields = {}

    if step == "name":
        update_fields = {"name": text, "step": "age"}
        await update.message.reply_text("How old are you?")
    elif step == "age":
        if not text.isdigit():
            return await update.message.reply_text("Please enter a valid age.")
        update_fields = {"age": int(text), "step": "gender"}
        await update.message.reply_text("Your gender?")
    elif step == "gender":
        update_fields = {"gender": text, "step": "bio"}
        await update.message.reply_text("Write a short bio.")
    elif step == "bio":
        update_fields = {"bio": text, "step": "preference"}
        await update.message.reply_text("Who are you looking for? (Male, Female, Any)")
    elif step == "preference":
        update_fields = {"preference": text, "step": "done"}
        await update.message.reply_text("✅ Profile saved! Type /match to find people.")
    else:
        await update.message.reply_text("Type /match to find people.")
        return

    users_col.update_one({"_id": uid}, {"$set": update_fields})

# Match command
async def match(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user = users_col.find_one({"_id": uid})
    if not user or user.get("step") != "done":
        return await update.message.reply_text("Complete your profile first using /start.")

    preference = user.get("preference", "any").lower()
    liked_ids = likes_col.find_one({"_id": uid}) or {"liked": [], "disliked": []}
    liked_set = set(liked_ids.get("liked", []) + liked_ids.get("disliked", []))

    matches = users_col.find({
        "_id": {"$ne": uid, "$nin": list(liked_set)},
        "step": "done",
        "gender": {"$regex": preference if preference != "any" else ".*", "$options": "i"}
    })

    for match in matches:
        context.user_data["current_match"] = match["_id"]
        profile = f"Name: {match['name']}\nAge: {match['age']}\nGender: {match['gender']}\nBio: {match['bio']}"
        buttons = [[
            InlineKeyboardButton("❤️ Like", callback_data="like"),
            InlineKeyboardButton("❌ Pass", callback_data="pass")
        ]]
        await update.message.reply_text(profile, reply_markup=InlineKeyboardMarkup(buttons))
        return

    await update.message.reply_text("No more matches right now.")

# Button callback (Like/Pass)
async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    target_id = context.user_data.get("current_match")

    if not target_id:
        return await query.edit_message_text("No match selected.")

    likes_col.update_one({"_id": uid}, {"$setOnInsert": {"liked": [], "disliked": []}}, upsert=True)

    if query.data == "like":
        likes_col.update_one({"_id": uid}, {"$addToSet": {"liked": target_id}})
        target_likes = likes_col.find_one({"_id": target_id})
        if target_likes and uid in target_likes.get("liked", []):
            await query.edit_message_text("🎉 It's a match! You can now chat.")
            await context.bot.send_message(chat_id=target_id, text="🎉 You matched! Start chatting now!")
            chats_col.update_one(
                {"$or": [{"user1": uid, "user2": target_id}, {"user1": target_id, "user2": uid}]},
                {"$setOnInsert": {"user1": uid, "user2": target_id, "messages": []}},
                upsert=True
            )
        else:
            await query.edit_message_text("Liked! Type /match for more.")
    elif query.data == "pass":
        likes_col.update_one({"_id": uid}, {"$addToSet": {"disliked": target_id}})
        await query.edit_message_text("Skipped. Type /match to continue.")

# Handle photo sharing
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    chat = chats_col.find_one({"$or": [{"user1": uid}, {"user2": uid}]})
    if chat:
        partner_id = chat["user2"] if chat["user1"] == uid else chat["user1"]
        photo = update.message.photo[-1].file_id
        await context.bot.send_photo(chat_id=partner_id, photo=photo)

# --- Webhook Setup ---
@app.on_event("startup")
async def on_startup():
    await telegram_app.initialize()  # ✅ REQUIRED for webhook apps
    await telegram_app.bot.set_webhook(f"{WEBHOOK_URL}/webhook")

@app.post("/webhook")
async def telegram_webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, telegram_app.bot)
    await telegram_app.process_update(update)
    return {"ok": True}

# --- Handlers ---
telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(CommandHandler("match", match))
telegram_app.add_handler(MessageHandler(filters.TEXT, handle_message))
telegram_app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
telegram_app.add_handler(CallbackQueryHandler(button))
