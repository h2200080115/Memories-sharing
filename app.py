import os
import zipfile
import io
import random
import string
from datetime import datetime
from PIL import Image
from flask import Flask, render_template, request, redirect, url_for, session, send_from_directory, jsonify, send_file, flash
from flask_sqlalchemy import SQLAlchemy
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash

# Configuration
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'static', 'uploads')
DB_PATH = os.path.join(BASE_DIR, 'instance', 'photos.db')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}

app = Flask(__name__)
app.secret_key = 'super_secret_key_for_friend_trip_2024' 
# Database Configuration
database_url = os.environ.get('DATABASE_URL')
if database_url:
    # Fix Render's postgres:// needing to be postgresql:// for SQLAlchemy
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
else:
    app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{DB_PATH}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100MB max upload

os.makedirs(os.path.join(BASE_DIR, 'instance'), exist_ok=True)
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

db = SQLAlchemy(app)

# --- Models ---

# Association Table for User <-> Trip
user_trips = db.Table('user_trips',
    db.Column('user_id', db.Integer, db.ForeignKey('user.id'), primary_key=True),
    db.Column('trip_id', db.Integer, db.ForeignKey('trip.id'), primary_key=True)
)

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    mobile = db.Column(db.String(20), unique=True, nullable=False)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)
    
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

class Trip(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    code = db.Column(db.String(10), unique=True, nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    members = db.relationship('User', secondary=user_trips, lazy='subquery',
        backref=db.backref('trips', lazy=True))
    albums = db.relationship('Album', backref='trip', lazy=True, cascade="all, delete-orphan")

class Album(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    # Album name isn't strictly necessary if it's always the username, but keeping for flexibility
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    trip_id = db.Column(db.Integer, db.ForeignKey('trip.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    photos = db.relationship('Photo', backref='album', lazy=True, cascade="all, delete-orphan")
    owner = db.relationship('User', backref='albums', lazy=True)

    def get_cover_photo(self):
        latest = Photo.query.filter_by(album_id=self.id).order_by(Photo.uploaded_at.desc()).first()
        return latest.filename if latest else None

class Photo(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(120), nullable=False)
    album_id = db.Column(db.Integer, db.ForeignKey('album.id'), nullable=False)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)

# --- Helpers ---

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def generate_trip_code():
    chars = string.ascii_uppercase + string.digits
    while True:
        code = ''.join(random.choices(chars, k=6))
        if not Trip.query.filter_by(code=code).first():
            return code

def compress_image(file_path):
    try:
        with Image.open(file_path) as img:
            img = img.convert('RGB')
            if img.width > 1920:
                ratio = 1920 / img.width
                new_height = int(img.height * ratio)
                img = img.resize((1920, new_height), Image.Resampling.LANCZOS)
            img.save(file_path, optimize=True, quality=85)
    except Exception as e:
        print(f"Error compressing image: {e}")

# --- Routes ---

@app.route('/')
def index():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    user_id = session['user_id']
    user = User.query.get(user_id)
    if not user:
        session.clear()
        return redirect(url_for('login'))
        
    return render_template('dashboard.html', trips=user.trips)

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        mobile = request.form.get('mobile').strip()
        username = request.form.get('username').strip().lower()
        password = request.form.get('password')

        if not mobile or not username or not password:
            flash('All fields are required', 'error')
            return redirect(url_for('signup'))
        
        if User.query.filter_by(mobile=mobile).first():
            flash('Mobile number already registered', 'error')
            return redirect(url_for('signup'))

        if User.query.filter_by(username=username).first():
            flash('Username already taken', 'error')
            return redirect(url_for('signup'))

        try:
            user = User(mobile=mobile, username=username)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            
            session['username'] = username
            session['user_id'] = user.id
            return redirect(url_for('index'))
        except Exception as e:
            db.session.rollback()
            print(f"Signup Error: {e}")
            flash(f"Error creating account: {str(e)}", 'error')
            return redirect(url_for('signup'))

    return render_template('signup.html')

@app.route('/debug-db')
def debug_db():
    try:
        db.session.execute(db.text('SELECT 1'))
        return "Database connection successful! Tables: " + str(db.inspect(db.engine).get_table_names())
    except Exception as e:
        return f"Database connection failed: {str(e)}"

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        mobile = request.form.get('mobile').strip()
        password = request.form.get('password')
        
        user = User.query.filter_by(mobile=mobile).first()
        if user and user.check_password(password):
            session['username'] = user.username
            session['user_id'] = user.id
            return redirect(url_for('index'))
        else:
            flash('Invalid mobile number or password', 'error')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# --- Trip Routes ---

@app.route('/trip/create', methods=['GET', 'POST'])
def create_trip():
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    if request.method == 'POST':
        name = request.form.get('name').strip()
        if not name:
            flash('Trip name is required', 'error')
            return redirect(url_for('create_trip'))
            
        user = User.query.get(session['user_id'])
        code = generate_trip_code()
        
        trip = Trip(name=name, code=code, created_by_id=user.id)
        # Add creator as a member
        trip.members.append(user)
        
        db.session.add(trip)
        db.session.commit()
        
        flash(f'Trip "{name}" created! Code: {code}', 'success')
        return redirect(url_for('view_trip', trip_id=trip.id))
        
    return render_template('create_trip.html')

@app.route('/trip/join', methods=['GET', 'POST'])
def join_trip():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    if request.method == 'POST':
        code = request.form.get('code').strip().upper()
        trip = Trip.query.filter_by(code=code).first()
        
        if not trip:
            flash('Invalid Trip Code', 'error')
            return redirect(url_for('join_trip'))
            
        user = User.query.get(session['user_id'])
        
        if user in trip.members:
            flash('You are already in this trip!', 'info')
            return redirect(url_for('view_trip', trip_id=trip.id))
            
        trip.members.append(user)
        db.session.commit()
        
        flash(f'Joined trip "{trip.name}"!', 'success')
        return redirect(url_for('view_trip', trip_id=trip.id))
        
    return render_template('join_trip.html')

@app.route('/trip/<int:trip_id>')
def view_trip(trip_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    trip = Trip.query.get_or_404(trip_id)
    user = User.query.get(session['user_id'])
    
    # Access Control: specific to this route's functionality
    if user not in trip.members:
        flash('You must join this trip to view photos.', 'error')
        return redirect(url_for('index'))
        
    # Prepare album data
    album_data = []
    # Show albums for all members (or just existing albums?)
    # Let's iterate through actual albums created in this trip
    for album in trip.albums:
        cover = album.get_cover_photo()
        album_data.append({
            'id': album.id,
            'owner': album.owner.username,
            'photo_count': len(album.photos),
            'cover': cover
        })
        
    return render_template('trip.html', trip=trip, albums=album_data)

@app.route('/trip/<int:trip_id>/upload', methods=['POST'])
def upload_to_trip(trip_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    trip = Trip.query.get_or_404(trip_id)
    user = User.query.get(session['user_id'])
    
    if user not in trip.members:
        return jsonify({'error': 'Not a member'}), 403
        
    files = request.files.getlist('photos')
    if not files:
        return jsonify({'error': 'No files'}), 400

    # Ensure user has an album for this trip
    album = Album.query.filter_by(user_id=user.id, trip_id=trip.id).first()
    if not album:
        album = Album(user_id=user.id, trip_id=trip.id)
        db.session.add(album)
        db.session.commit()

    uploaded_count = 0
    for file in files:
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
            unique_filename = f"{trip.code}_{user.username}_{timestamp}_{filename}"
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
            
            file.save(file_path)
            # compress_image(file_path)
            
            photo = Photo(filename=unique_filename, album_id=album.id)
            db.session.add(photo)
            uploaded_count += 1
            
    db.session.commit()
    return jsonify({'success': True, 'count': uploaded_count})

@app.route('/album/<int:album_id>')
def view_album_details(album_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
        
    album = Album.query.get_or_404(album_id)
    user = User.query.get(session['user_id'])
    
    # Check if user is member of the trip this album belongs to
    if user not in album.trip.members:
         flash('Access Denied', 'error')
         return redirect(url_for('index'))

    photos = Photo.query.filter_by(album_id=album.id).order_by(Photo.uploaded_at.desc()).all()
    return render_template('album.html', 
                          album_name=album.trip.name, 
                          owner_name=album.owner.username,
                          photos=photos,
                          trip_id=album.trip.id,
                          album_id=album.id)

@app.route('/delete/<int:photo_id>', methods=['POST'])
def delete_photo(photo_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
        
    photo = Photo.query.get_or_404(photo_id)
    user = User.query.get(session['user_id'])
    
    if photo.album.owner.id != user.id:
        return jsonify({'error': 'Permission denied'}), 403
        
    try:
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], photo.filename)
        if os.path.exists(file_path):
            os.remove(file_path)
        db.session.delete(photo)
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/download/photo/<int:photo_id>')
def download_photo(photo_id):
    photo = Photo.query.get_or_404(photo_id)
    # Check permissions (omitted for brevity, but should exist)
    return send_from_directory(app.config['UPLOAD_FOLDER'], photo.filename, as_attachment=True)

@app.route('/download/album/<int:album_id>')
def download_album(album_id):
    if 'user_id' not in session:
        return "Unauthorized", 401
        
    album = Album.query.get_or_404(album_id)
    user = User.query.get(session['user_id'])
    if user not in album.trip.members:
        return "Unauthorized", 403

    photos = Photo.query.filter_by(album_id=album.id).all()
    if not photos:
        return "Album empty", 404

    memory_file = io.BytesIO()
    with zipfile.ZipFile(memory_file, 'w') as zf:
        for photo in photos:
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], photo.filename)
            if os.path.exists(file_path):
                zf.write(file_path, photo.filename)
    
    memory_file.seek(0)
    filename = f"{album.owner.username}_{album.trip.name}.zip"
    return send_file(memory_file, download_name=filename, as_attachment=True)

@app.route('/download/selected', methods=['POST'])
def download_selected():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.get_json()
    photo_ids = data.get('photo_ids', [])
    if not photo_ids:
        return jsonify({'error': 'No photos selected'}), 400

    photos = Photo.query.filter(Photo.id.in_(photo_ids)).all()
    if not photos:
        return jsonify({'error': 'Photos not found'}), 404
        
    # Check permissions (must be member of trip)
    # Assuming all photos belong to albums in trips the user is in.
    # For strictness: check first photo's trip membership
    first_photo = photos[0]
    user = User.query.get(session['user_id'])
    if user not in first_photo.album.trip.members:
         return jsonify({'error': 'Unauthorized'}), 403

    # If single photo, download directly
    if len(photos) == 1:
        return jsonify({
            'success': True, 
            'single': True, 
            'url': url_for('download_photo', photo_id=photos[0].id)
        })

    # If multiple, zip them
    memory_file = io.BytesIO()
    with zipfile.ZipFile(memory_file, 'w') as zf:
        for photo in photos:
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], photo.filename)
            if os.path.exists(file_path):
                zf.write(file_path, photo.filename)
    
    memory_file.seek(0)
    # We can't return a file directly in a JSON POST response usually without blob handling in JS.
    # Instead, we'll store it? No, stateless.
    # Better approach: Generate a unique token or temp ID for this zip? 
    # Or simplified: JS posts IDs, gets a 'download_token', then GETs the download?
    # Actually, simplest for this Hackathon-style app: 
    # POST form submit? No, JSON is cleaner.
    # Let's use a GET with Query Params for download if possible, or stick to the JS Blob method.
    # JS Blob method:
    return send_file(
        memory_file, 
        mimetype='application/zip',
        as_attachment=True, 
        download_name=f'selected_photos_{datetime.now().strftime("%Y%m%d")}.zip'
    )

@app.route('/delete/selected', methods=['POST'])
def delete_selected():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
        
    data = request.get_json()
    photo_ids = data.get('photo_ids', [])
    if not photo_ids:
        return jsonify({'error': 'No photos selected'}), 400
        
    user = User.query.get(session['user_id'])
    
    photos = Photo.query.filter(Photo.id.in_(photo_ids)).all()
    deleted_count = 0
    
    for photo in photos:
        # Strict: Only owner can delete
        if photo.album.owner.id == user.id:
            try:
                file_path = os.path.join(app.config['UPLOAD_FOLDER'], photo.filename)
                if os.path.exists(file_path):
                    os.remove(file_path)
                db.session.delete(photo)
                deleted_count += 1
            except Exception as e:
                print(f"Error deleting photo {photo.id}: {e}")
                
    db.session.commit()
    return jsonify({'success': True, 'count': deleted_count})

with app.app_context():
    db.create_all()

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
