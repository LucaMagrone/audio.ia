
import os
import openai
import stripe
from flask import Flask, request, render_template, redirect, url_for, session, send_from_directory, abort
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from gtts import gTTS
from dotenv import load_dotenv
from datetime import datetime, timedelta

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY")
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///users.db'
app.config['MAX_CONTENT_LENGTH'] = 64 * 1024 * 1024
db = SQLAlchemy(app)

openai.api_key = os.getenv("OPENAI_API_KEY")
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(150), nullable=False)
    is_premium = db.Column(db.Boolean, default=False)
    last_reset = db.Column(db.DateTime, default=datetime.utcnow)
    uploads_today = db.Column(db.Integer, default=0)

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/abbonati')
def abbonati():
    return render_template('abbonati.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        email = request.form['email']
        password = generate_password_hash(request.form['password'])
        if User.query.filter_by(email=email).first():
            return "Email già registrata."
        user = User(email=email, password=password)
        db.session.add(user)
        db.session.commit()
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        user = User.query.filter_by(email=email).first()
        if user and check_password_hash(user.password, password):
            session['user_id'] = user.id
            return redirect(url_for('home'))
        return "Login fallito."
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    return redirect(url_for('login'))

@app.route('/upload', methods=['POST'])
def upload():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    user = User.query.get(session['user_id'])

    now = datetime.utcnow()
    if not user.is_premium:
        if (now - user.last_reset).days >= 1:
            user.uploads_today = 0
            user.last_reset = now
        if user.uploads_today >= 2:
            return render_template("limite.html")

    if 'audio' not in request.files:
        return "Nessun file audio inviato."

    file = request.files['audio']
    title = request.form.get('title', 'Untitled')
    lang = request.form.get('language', 'it')

    transcription = openai.Audio.transcribe("whisper-1", file)

    if lang == "it":
        prompt = f"""
        Agisci come un assistente intelligente.
        Analizza il seguente messaggio vocale e restituisci:

        1. 🔹 Punto principale: Di cosa parla?
        2. ✅ Azioni richieste: Se ci sono istruzioni, elencale.
        3. 🎭 Tono: Urgente, calmo, poetico, ecc.
        4. 🧠 Riassunto completo: Max 5 frasi semplici.

        Testo trascritto:
        """{transcription['text']}"""
        """
    else:
        prompt = f"""
        Act as an intelligent assistant.
        Analyze the following voice note and return:

        1. 🔹 Main point: What is it about?
        2. ✅ Required actions: Any instructions?
        3. 🎭 Tone: Urgent, calm, poetic, etc.
        4. 🧠 Full summary: Max 5 simple sentences.

        Transcribed text:
        """{transcription['text']}"""
        """

    response = openai.ChatCompletion.create(
        model="gpt-4",
        messages=[{"role": "user", "content": prompt}]
    )
    summary = response['choices'][0]['message']['content']

    tts = gTTS(text=summary, lang=lang)
    filename = f"{title.replace(' ', '_')}_{user.id}.mp3"
    audio_path = os.path.join("protected_audio", filename)
    os.makedirs("protected_audio", exist_ok=True)
    tts.save(audio_path)

    if not user.is_premium:
        user.uploads_today += 1
    db.session.commit()

    return render_template("risultato.html",
                           title=title,
                           transcription=transcription['text'],
                           summary=summary,
                           audio_url=url_for("protected_file", filename=filename))

@app.route('/audio/<filename>')
def protected_file(filename):
    if 'user_id' not in session:
        abort(403)
    return send_from_directory('protected_audio', filename)

@app.route('/checkout_monthly')
def checkout_monthly():
    session_url = stripe.checkout.Session.create(
        payment_method_types=["card"],
        line_items=[{
            "price_data": {
                "currency": "eur",
                "product_data": {"name": "Audio.ia Premium (Mensile)"},
                "unit_amount": 1000,
                "recurring": {"interval": "month"}
            },
            "quantity": 1,
        }],
        mode="subscription",
        success_url=request.host_url + "success",
        cancel_url=request.host_url + "cancel"
    )
    return redirect(session_url.url)

@app.route('/checkout_annual')
def checkout_annual():
    session_url = stripe.checkout.Session.create(
        payment_method_types=["card"],
        line_items=[{
            "price_data": {
                "currency": "eur",
                "product_data": {"name": "Audio.ia Premium (Annuale)"},
                "unit_amount": 10000,
                "recurring": {"interval": "year"}
            },
            "quantity": 1,
        }],
        mode="subscription",
        success_url=request.host_url + "success",
        cancel_url=request.host_url + "cancel"
    )
    return redirect(session_url.url)

@app.route('/webhook', methods=['POST'])
def stripe_webhook():
    payload = request.data
    sig_header = request.headers.get('stripe-signature')
    endpoint_secret = os.getenv('STRIPE_WEBHOOK_SECRET')
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, endpoint_secret)
    except Exception as e:
        return str(e), 400

    if event['type'] == 'checkout.session.completed':
        session_data = event['data']['object']
        print("Pagamento confermato via webhook.")
    return '', 200

@app.route('/success')
def success():
    user = User.query.get(session['user_id'])
    user.is_premium = True
    db.session.commit()
    return "Pagamento riuscito. Ora sei Premium!"

@app.route('/cancel')
def cancel():
    return "Pagamento annullato."

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)
