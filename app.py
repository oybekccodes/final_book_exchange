from flask import Flask, request, session, redirect, url_for, render_template_string,render_template,jsonify
from pymongo import MongoClient
from werkzeug.security import generate_password_hash, check_password_hash
from bson import ObjectId
import os
import uuid
from werkzeug.utils import secure_filename
from text_generation import generate_description
import io
from flask import send_file
import google.generativeai as genai
from PIL import Image


app = Flask(__name__)
app.secret_key = 'your-secret-key'
app.config["MONGO_URI"] = mongodb+srv://oybeksejong:FvOv45PCpEnF7aa0@cluster0.uzxrtri.mongodb.net/

# MongoDB Setup
client = MongoClient("mongodb://localhost:27017/")
db = client['book_exchange']    
users_collection = db['users']
books_collection=db['books']
messages_collection=db['messages']



@app.route('/')
def home():
    username = session.get('username')
    return render_template('home.html', username=username)


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        if users_collection.find_one({'username': username}):
            return "Username already exists."

        hashed_pw = generate_password_hash(password)
        users_collection.insert_one({'username': username, 'password': hashed_pw})
        return redirect(url_for('login'))

    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        user = users_collection.find_one({'username': username})
        if user and check_password_hash(user['password'], password):
            session['username'] = username
            return redirect(url_for('home'))

        return "Invalid credentials"

    return render_template('login.html')


@app.route('/logout')
def logout():
    session.pop('username', None)
    return redirect(url_for('home'))


@app.route('/post_book', methods=['GET', 'POST'])
def post_book():
    if 'username' not in session:
        return redirect(url_for('login'))

    if request.method == 'POST':
        title = request.form['title']
        author = request.form['author']
        description = request.form.get('description', '')
        image = request.files.get('image')
        image_filename = None  # Default in case no image is uploaded

        if image and allowed_file(image.filename):
            unique_name = str(uuid.uuid4()) + "_" + secure_filename(image.filename)
            image_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_name)
            image.save(image_path)
            image_filename = unique_name

        books_collection.insert_one({
            'title': title,
            'author': author,
            'description': description,
            'owner': session['username'],
            'available': True,
            'image_filename': image_filename
        })

        return redirect(url_for('view_books'))

    return render_template('post_book.html')

# Config and helper
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['ALLOWED_EXTENSIONS'] = {'png', 'jpg', 'jpeg', 'gif'}

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']


# Route for browsing/searching books (GET only)
@app.route('/borrow', methods=['GET'])
def view_books():
    query = request.args.get('q', '').strip()
    if query:
        books = books_collection.find({
            "$and": [
                {
                    "$or": [
                        {"title": {"$regex": query, "$options": "i"}},
                        {"author": {"$regex": query, "$options": "i"}}
                    ]
                },
                {"available": True}
            ]
        })
    else:
        books = books_collection.find({"available": True})

    return render_template('browse_books.html', books=books, query=query)

from datetime import datetime

@app.route('/borrow/<book_id>', methods=['POST'])
def borrow_book(book_id):
    if 'username' not in session:
        return redirect(url_for('login'))

    book = books_collection.find_one({"_id": ObjectId(book_id)})
    if not book:
        return "Book not found."

    if book['owner'] == session['username']:
        return "You can't borrow your own book."

    # Prevent duplicate request
    existing_request = db.borrow_requests.find_one({
        "book_id": ObjectId(book_id),
        "borrower": session['username'],
        "status": "pending"
    })

    if existing_request:
        return "You’ve already sent a request for this book."


    # Create a borrow request
    db.borrow_requests.insert_one({
        "book_id": ObjectId(book_id),
        "book_title": book['title'],
        "borrower": session['username'],
        "owner": book['owner'],
        "status": "pending",
        "timestamp": datetime.utcnow()
    })


    return render_template('borrow_book.html')

@app.route('/my_requests')
def my_requests():
    if 'username' not in session:
        return redirect(url_for('login'))

    requests = db.borrow_requests.find({
        "owner": session['username'],
        "status": "pending"
    })
    return render_template("my_requests.html", requests=requests)

@app.route('/handle_request/<request_id>/<action>', methods=['POST'])
def handle_request(request_id, action):
    if 'username' not in session:
        return redirect(url_for('login'))

    req = db.borrow_requests.find_one({"_id": ObjectId(request_id)})
    if not req or req['owner'] != session['username']:
        return "Unauthorized or invalid request."

    if action == 'accept':
        # Mark the book as borrowed
        books_collection.update_one(
            {"_id": req['book_id']},
            {"$set": {
                "available": False,
                "borrower": req['borrower']
            }}
        )
        db.borrow_requests.update_one(
            {"_id": ObjectId(request_id)},
            {"$set": {"status": "accepted"}}
        )
    elif action == 'reject':
        db.borrow_requests.update_one(
            {"_id": ObjectId(request_id)},
            {"$set": {"status": "rejected"}}
        )
    return redirect(url_for('my_requests'))


# Return Book
@app.route('/return/<book_id>', methods=['POST'])
def return_book(book_id):
    if 'username' not in session:
        return redirect(url_for('login'))

    book = books_collection.find_one({'_id': ObjectId(book_id)})
    if not book or book.get('borrower') != session['username']:
        return "You can’t return this book."

    books_collection.update_one(
        {'_id': ObjectId(book_id)},
        {'$set': {'available': True}, '$unset': {'borrower': ""}}
    )
    return redirect(url_for('my_books'))


# User Dashboard
@app.route('/my_books')
def my_books():
    if 'username' not in session:
        return redirect(url_for('login'))

    username = session['username']
    borrowed_books = list(books_collection.find({'borrower': username}))
    posted_books = list(books_collection.find({'owner': username}))

    return render_template('my_books.html', borrowed_books=borrowed_books, posted_books=posted_books)


# Chat System
@app.route('/chat/<book_id>', methods=['GET', 'POST'])
def chat(book_id):
    if 'username' not in session:
        return redirect(url_for('login'))

    user = session['username']
    book = books_collection.find_one({'_id': ObjectId(book_id)})

    if not book:
        return "Book not found."

    if user != book['owner'] and user != book.get('borrower'):
        return "You don’t have permission to chat on this book."

    if request.method == 'POST':
        message = request.form['message']
        if message:
            messages_collection.insert_one({
                'book_id': book_id,
                'sender': user,
                'recipient': book['borrower'] if user == book['owner'] else book['owner'],
                'text': message
            })

    messages = list(messages_collection.find({'book_id': book_id}))

  
    return render_template('chat.html', book=book, messages=messages)


@app.route('/profile')
def profile():
    if 'username' not in session:
        return redirect(url_for('login'))

    username = session['username']
    posted_books = list(books_collection.find({'owner': username}))
    borrowed_books = list(books_collection.find({'borrower': username}))
    pending_requests = db.borrow_requests.find({
        'owner': username,
        'status': 'pending'
    })

    return render_template(
        'profile.html',
        username=username,
        posted_books=posted_books,
        borrowed_books=borrowed_books,
        requests=pending_requests
    )



@app.route('/edit_book/<book_id>', methods=['GET', 'POST'])
def edit_book(book_id):
    if 'username' not in session:
        return redirect(url_for('login'))

    book = books_collection.find_one({'_id': ObjectId(book_id)})

    if not book or book['owner'] != session['username']:
        return "Unauthorized access."

    if request.method == 'POST':
        title = request.form['title']
        author = request.form['author']
        description = request.form['description']

        books_collection.update_one(
            {'_id': ObjectId(book_id)},
            {'$set': {
                'title': title,
                'author': author,
                'description': description
            }}
        )
        return redirect(url_for('my_books'))

    return render_template('edit_book.html', book=book)

@app.route('/delete_book/<book_id>', methods=['POST'])
def delete_book(book_id):
    if 'username' not in session:
        return redirect(url_for('login'))

    book = books_collection.find_one({'_id': ObjectId(book_id)})

    if not book or book['owner'] != session['username']:
        return "Unauthorized action."

    books_collection.delete_one({'_id': ObjectId(book_id)})
    return redirect(url_for('my_books'))


@app.route('/notifications')
def notifications():
    if 'username' not in session:
        return jsonify({"error": "Unauthorized"}), 401

    username = session['username']
    pending_requests_count = db.borrow_requests.count_documents({
        "owner": username,
        "status": "pending"
    })

    unread_messages_count = db.chat_messages.count_documents({
        "receiver": username,
        "read": False
    })

    return jsonify({
        "pending_requests": pending_requests_count,
        "unread_messages": unread_messages_count
    })


genai.configure(api_key="AIzaSyDwn13KMvT9yIfececfQ_xbBfxe2gpM0-4")

@app.route('/ask_gemini', methods=['GET', 'POST'])
def ask_gemini():
    if request.method == 'POST':
        data = request.get_json()
        prompt = data.get('prompt', '')

        try:
            response = generate_description(prompt)
            return jsonify({'response': response})
        except Exception as e:
            return jsonify({'response': f"Error: {str(e)}"}), 500

    return render_template('ask_gemini.html')




if __name__ == '__main__':
    app.run(debug=True)
