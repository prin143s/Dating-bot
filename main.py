import os
from fastapi import FastAPI, Request
from pymongo import MongoClient
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)

# === Environment Setup ===
BOT_TOKEN = os.environ["BOT_TOKEN"]
MONGODB_URI = os.environ["MONGODB_URI"]
WEBHOOK_URL = os.environ["WEBHOOK_URL"]  # Set this in Render as: https://your-app-name.onrender.com/webhook

client = MongoClient(MONGODB_URI)
db = client["dating_bot"]
users_col = db["users"]
likes_col = db["likes"]

# === Bot Logic ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    users_col.update_one({"_id": uid}, {"$set": {"step": "name"}}, upsert=True)
    await update.message.reply_text("Welcome! What's your name?")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text
    user = users_col.find_one({"_id": uid})
    if not user:
        return await update.message.reply_text("Please type /start to begin.")
    step = user.get("step")
    update_fields = {}
    if step == "name":
        update_fields = {"name": text, "step": "age"}
        await update.message.reply_text("Great! How old are you?")
    elif step == "age":
        if not text.isdigit():
            return await update.message.reply_text("Enter a valid age.")
        update_fields = {"age": int(text), "step": "gender"}
        await update.message.reply_text("What’s your gender?")
    elif step == "gender":
        update_fields = {"gender": text, "step": "bio"}
        await update.message.reply_text("Short bio?")
    elif step == "bio":
        update_fields = {"bio": text, "step": "preference"}
        await update.message.reply_text("Who are you looking for? (e.g., Male, Female, Any)")
    elif step == "preference":
        update_fields = {"preference": text, "step": "done"}
        await update.message.reply_text("Profile done! Type /match to find people.")
    else:
        await update.message.reply_text("Type /match to find people.")
        return
    users_col.update_one({"_id": uid}, {"$set": update_fields})

async def match(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user = users_col.find_one({"_id": uid})
    if not user or user.get("step") != "done":
        return await update.message.reply_text("Complete your profile with /start.")

    preference = user.get("preference", "Any").lower()
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

    await update.message.reply_text("No more matches available now.")

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
            await query.edit_message_text("🎉 It's a match!")
        else:
            await query.edit_message_text("Liked! Type /match for another.")
    elif query.data == "pass":
        likes_col.update_one({"_id": uid}, {"$addToSet": {"disliked": target_id}})
        await query.edit_message_text("Skipped. Type /match to continue.")

# === Application Setup ===
app = FastAPI()
telegram_app = Application.builder().token(BOT_TOKEN).updater(None).build()


telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(CommandHandler("match", match))
telegram_app.add_handler(MessageHandler(filters.TEXT, handle_message))
telegram_app.add_handler(CallbackQueryHandler(button))

@app.on_event("startup")
async def startup():
    await telegram_app.initialize()
    await telegram_app.bot.set_webhook(url=f"{WEBHOOK_URL}/webhook")
    await telegram_app.start()

@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, telegram_app.bot)
    await telegram_app.process_update(update)
    return "ok"
