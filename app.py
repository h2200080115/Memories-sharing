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
import boto3
from botocore.exceptions import ClientError

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

# AWS S3 Configuration
app.config['S3_BUCKET'] = os.environ.get('S3_BUCKET')
app.config['S3_KEY'] = os.environ.get('S3_KEY')
app.config['S3_SECRET'] = os.environ.get('S3_SECRET')
app.config['S3_REGION'] = os.environ.get('S3_REGION', 'us-east-1')

s3 = boto3.client(
    's3',
    aws_access_key_id=app.config['S3_KEY'],
    aws_secret_access_key=app.config['S3_SECRET'],
    region_name=app.config['S3_REGION']
)

os.makedirs(os.path.join(BASE_DIR, 'instance'), exist_ok=True)
# os.makedirs(UPLOAD_FOLDER, exist_ok=True) # No longer needed for S3

db = SQLAlchemy(app)

def get_s3_url(filename):
    if not filename: return None
    try:
        url = s3.generate_presigned_url(
            'get_object',
            Params={'Bucket': app.config['S3_BUCKET'], 'Key': filename},
            ExpiresIn=3600
        )
        return url
    except ClientError as e:
        print(f"Error generating S3 URL: {e}")
        return None

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
    password_hash = db.Column(db.String(256), nullable=False)
    
    def set_password(self, password):
        self.password_hash = generate_password_hash(password, method='pbkdf2:sha256')
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
        return get_s3_url(latest.filename) if latest else None

class Photo(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(120), nullable=False)
    album_id = db.Column(db.Integer, db.ForeignKey('album.id'), nullable=False)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def url(self):
        return get_s3_url(self.filename)

# --- Helpers ---

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def generate_trip_code():
    chars = string.ascii_uppercase + string.digits
    while True:
        code = ''.join(random.choices(chars, k=6))
        if not Trip.query.filter_by(code=code).first():
            return code

# def compress_image(file_path):
#     # Compression logic would need to happen in-memory or temp file for S3
#     pass

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
        # Check DB
        db.session.execute(db.text('SELECT 1'))
        db_status = "Database connection successful! Tables: " + str(db.inspect(db.engine).get_table_names())
        
        # Check S3
        s3.list_objects_v2(Bucket=app.config['S3_BUCKET'], MaxKeys=1)
        s3_status = "S3 connection successful!"
        
        return f"{db_status}<br>{s3_status}"
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

@app.route('/trip/<int:trip_id>/remove_member/<int:member_id>', methods=['POST'])
def remove_member(trip_id, member_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    trip = Trip.query.get_or_404(trip_id)
    current_user = User.query.get(session['user_id'])
    
    # Only trip creator can remove members
    if trip.created_by_id != current_user.id:
        return jsonify({'error': 'Only trip admin can remove members'}), 403
        
    member_to_remove = User.query.get_or_404(member_id)
    
    # Cannot remove yourself (the creator) via this route
    if member_to_remove.id == current_user.id:
        return jsonify({'error': 'Cannot remove yourself'}), 400
        
    if member_to_remove in trip.members:
        trip.members.remove(member_to_remove)
        db.session.commit()
        return jsonify({'success': True})
    
    return jsonify({'error': 'User not in trip'}), 404

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
    errors = []
    
    for file in files:
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
            unique_filename = f"{trip.code}_{user.username}_{timestamp}_{filename}"
            
            try:
                s3.upload_fileobj(
                    file,
                    app.config['S3_BUCKET'],
                    unique_filename,
                    ExtraArgs={'ContentType': file.content_type}
                )
                
                photo = Photo(filename=unique_filename, album_id=album.id)
                db.session.add(photo)
                uploaded_count += 1
            except Exception as e:
                print(f"Upload error: {e}")
                errors.append(str(e))
                continue
            
    db.session.commit()
    
    if uploaded_count == 0 and errors:
        return jsonify({'success': False, 'error': "Upload failed: " + "; ".join(errors)})
        
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
    # Check if current user is the trip creator
    is_trip_creator = (user.id == album.trip.created_by_id)
    
    return render_template('album.html', 
                          album_name=album.trip.name, 
                          owner_name=album.owner.username,
                          photos=photos,
                          trip_id=album.trip.id,
                          album_id=album.id,
                          is_trip_creator=is_trip_creator)

@app.route('/delete/<int:photo_id>', methods=['POST'])
def delete_photo(photo_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
        
    photo = Photo.query.get_or_404(photo_id)
    user = User.query.get(session['user_id'])
    
    # Allow if user owns the photo OR user created the trip
    is_creator = (photo.album.trip.created_by_id == user.id)
    is_owner = (photo.album.owner.id == user.id)
    
    if not (is_owner or is_creator):
        return jsonify({'error': 'Permission denied'}), 403
        
    try:
        s3.delete_object(Bucket=app.config['S3_BUCKET'], Key=photo.filename)
        db.session.delete(photo)
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/download/photo/<int:photo_id>')
def download_photo(photo_id):
    photo = Photo.query.get_or_404(photo_id)
    # Redirect to presigned URL
    url = get_s3_url(photo.filename)
    if url:
        return redirect(url)
    return "Error generating download link", 500

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
            try:
                file_obj = s3.get_object(Bucket=app.config['S3_BUCKET'], Key=photo.filename)
                file_content = file_obj['Body'].read()
                zf.writestr(photo.filename, file_content)
            except Exception as e:
                print(f"Error zipping {photo.filename}: {e}")
    
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
            try:
                file_obj = s3.get_object(Bucket=app.config['S3_BUCKET'], Key=photo.filename)
                file_content = file_obj['Body'].read()
                zf.writestr(photo.filename, file_content)
            except Exception as e:
                print(f"Error zipping {photo.filename}: {e}")
    
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
        # Check permissions for each photo
        is_creator = (photo.album.trip.created_by_id == user.id)
        is_owner = (photo.album.owner.id == user.id)
        
        if is_owner or is_creator:
            try:
                s3.delete_object(Bucket=app.config['S3_BUCKET'], Key=photo.filename)
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
