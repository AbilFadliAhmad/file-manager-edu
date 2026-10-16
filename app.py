import json
import os
import random
from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO, emit, join_room
import google.generativeai as genai
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'default_secret_key')
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URI', 'sqlite:///fallback.db')

# Khusus untuk tipe data Boolean, konversi string dari .env menjadi True/False asli
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = os.environ.get('TRACK_MODIFICATIONS', 'False').lower() in ('true', '1', 't')

db = SQLAlchemy(app)
socketio = SocketIO(app, cors_allowed_origins="*")

# Konfigurasi Gemini API (GANTI DENGAN API KEY ANDA)
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

generation_config = {
    "temperature": 0.7,
    "response_mime_type": "application/json", # Memaksa output berupa JSON murni
}
gemini_model = genai.GenerativeModel('gemini-3.5-flash-lite', generation_config=generation_config)

# --- MODEL DATABASE BARU ---
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(50), nullable=False)
    role = db.Column(db.String(10), nullable=False) # 'admin' atau 'client'
    
    # Kolom tugas yang ditambahkan langsung ke User
    task_title = db.Column(db.String(100), nullable=True)
    task_description = db.Column(db.Text, nullable=True)
    task_paths = db.Column(db.Text, nullable=True)

# --- 1. Model Score (Tambahkan unique=True pada user_id) ---
class Score(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), unique=True, nullable=False) # unique=True
    task_title = db.Column(db.String(100), nullable=False)
    score = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user = db.relationship('User', backref=db.backref('score_rel', uselist=False))

class Task(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text, nullable=False)
    expected_paths = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow) # <--- TAMBAHKAN INI

# --- INISIALISASI DATABASE & DATA DUMMY ---
with app.app_context():
    # db.create_all() hanya membuat tabel baru jika belum ada (tidak merusak data lama)
    db.create_all()

    # Hanya isi data dummy jika tabel User masih kosong
    if not User.query.first():
        # Buat user dummy
        db.session.add(User(username='admin', password='123', role='admin'))
        db.session.add(User(username='siswa', password='123', role='client'))
        db.session.add(User(username='siswa2', password='123', role='client'))

        # Buat beberapa tugas dummy di pool tugas
        tugas1 = Task(
            title='Tugas 1: Pengenalan Folder',
            description="Buatlah folder 'Latihan1' di Disk D, lalu buat file 'imam.txt'.",
            expected_paths=json.dumps(["/Data (D:)/Latihan1", "/Data (D:)/Latihan1/imam.txt"])
        )
        tugas2 = Task(
            title='Tugas 2: Struktur C',
            description="Buatlah folder 'Tugas_C' di Disk C, lalu buat file 'data.txt'.",
            expected_paths=json.dumps(["/Local Disk (C:)/Tugas_C", "/Local Disk (C:)/Tugas_C/data.txt"])
        )
        db.session.add_all([tugas1, tugas2])
        db.session.commit()


@app.route('/', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = User.query.filter_by(username=username, password=password).first()

        if user:
            session['user_id'] = user.id
            session['username'] = user.username
            session['role'] = user.role

            if user.role == 'admin':
                return redirect(url_for('admin_dashboard'))
            else:
                return redirect(url_for('client_dashboard'))

        # Mengirim variabel error ke template saat login gagal
        return render_template('login.html', error="Username atau Password Salah!")

    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/admin')
def admin_dashboard():
    if session.get('role') != 'admin':
        return redirect(url_for('login'))
    
    # Ambil data sisa tugas dan history skor
    sisa_tugas = Task.query.order_by(Task.created_at.desc()).all()
    history_score = Score.query.order_by(Score.created_at.desc()).all()
    
    return render_template('admin.html', username=session['username'], tasks=sisa_tugas, scores=history_score)

@app.route('/client')
def client_dashboard():
    if session.get('role') != 'client':
        return redirect(url_for('login'))
    
    user = User.query.get(session['user_id'])
    
    # Jika user belum punya tugas, ambil acak dari tabel Task
    if not user.task_paths:
        available_tasks = Task.query.all()
        if available_tasks:
            # Pilih tugas secara acak
            selected_task = random.choice(available_tasks)
            
            # Pindahkan data ke tabel User
            user.task_title = selected_task.title
            user.task_description = selected_task.description
            user.task_paths = selected_task.expected_paths
            
            # Hapus tugas dari tabel Task agar tidak bisa diambil user lain
            db.session.delete(selected_task)
            db.session.commit()
        else:
            # RENDER TEMPLATE ERROR / EMPTY STATE PROFESSIONAL
            return render_template('no_task.html', username=session.get('username'))

    # Ambil 1 data nilai dari table Score untuk user ini
    user_score = Score.query.filter_by(user_id=user.id).first()
    score_value = user_score.score if user_score else None

    # Kirim data tugas ke template
    task_data = {
        'id': user.id,
        'title': user.task_title,
        'description': user.task_description,
        'expected_paths': user.task_paths
    }
    return render_template(
        'student.html',
        username=session['username'],
        task=task_data,
        user_score=score_value
    )
# --- 3. Route /submit_task (Upsert / Update nilai jika sudah ada) ---
@app.route('/submit_task', methods=['POST'])
def submit_task():
    data = request.json
    user = User.query.get(session['user_id'])
    if not user or not user.task_paths:
        return jsonify({'error': 'Tugas tidak ditemukan'}), 404

    expected = set(json.loads(user.task_paths))
    submitted = set(data.get('paths', []))

    correct_matches = expected.intersection(submitted)
    score_value = 0
    if len(expected) > 0:
        score_value = int((len(correct_matches) / len(expected)) * 100)

    # Cek apakah nilai siswa sudah ada (Upsert)
    existing_score = Score.query.filter_by(user_id=user.id).first()
    if existing_score:
        existing_score.score = score_value
        existing_score.task_title = user.task_title
        existing_score.created_at = datetime.utcnow()
    else:
        new_score = Score(user_id=user.id, task_title=user.task_title, score=score_value)
        db.session.add(new_score)

    db.session.commit()

    # Kirim via WebSocket ke Guru
    socketio.emit('new_score', {
        'student': session['username'],
        'task': user.task_title,
        'score': score_value
    }, room='admin_room')

    return jsonify({'status': 'success', 'score': score_value})

# --- ROUTE GENERATOR TUGAS (TAMBAHKAN INI) ---
@app.route('/api/generate_tasks', methods=['POST'])
def generate_tasks():
    if session.get('role') != 'admin':
        return jsonify({'error': 'Unauthorized'}), 401
        
    data = request.json
    depth = data.get('depth', 2)
    count = data.get('count', 5)
    
    # Tuning Prompt untuk Gemini AI
    prompt = f"""
    Kamu adalah sistem pembuat tugas ujian praktek manajemen file.
    Tugasmu adalah membuat {count} tugas acak dengan tingkat kedalaman folder (kompleksitas) maksimal {depth} tingkat.
    
    ATURAN WAJIB:
    1. Base path harus selalu dimulai dengan salah satu dari ini: "/Local Disk (C:)" atau "/Data (D:)".
    2. Setiap tugas harus mensimulasikan skenario dunia nyata (misal: merapikan file tugas sekolah, membuat struktur proyek coding, arsip kantor, dll).
    3. 'expected_paths' HARUS merekam secara hierarkis semua folder dan file yang harus dibuat. 
       Contoh jika disuruh membuat 'index.txt' di dalam 'Web', maka expected_paths harus berisi ["/Data (D:)/Web", "/Data (D:)/Web/index.txt"].
       
    Hasilkan output HANYA dalam format array JSON murni dengan struktur berikut untuk setiap objek:
    [
      {{
        "title": "String (Judul Tugas yang menarik)",
        "description": "String (Instruksi langkah demi langkah yang jelas untuk siswa)",
        "expected_paths": ["String", "String"]
      }}
    ]
    """
    
    try:
        response = gemini_model.generate_content(prompt)
        # Parse output JSON dari Gemini
        tasks_data = json.loads(response.text)
        
        # Simpan ke tabel Task
        for t_data in tasks_data:
            new_task = Task(
                title=t_data['title'],
                description=t_data['description'],
                expected_paths=json.dumps(t_data['expected_paths'])
            )
            db.session.add(new_task)
            
        db.session.commit()
        return jsonify({'status': 'success', 'message': f'{count} Tugas berhasil digenerate!'})
        
    except Exception as e:
        print("Error Generating Tasks:", e)
        return jsonify({'error': str(e)}), 500

@app.route('/admin/members')
def admin_members():
    if session.get('role') != 'admin':
        return redirect(url_for('login'))
    
    # Ambil seluruh user (Admin dan Client)
    users = User.query.order_by(User.role.asc(), User.username.asc()).all()
    return render_template('members.html', username=session['username'], users=users)

@app.route('/api/members/add', methods=['POST'])
def add_member():
    if session.get('role') != 'admin':
        return jsonify({'error': 'Unauthorized'}), 401
    
    data = request.json
    username = data.get('username')
    password = data.get('password')
    
    if not username or not password:
        return jsonify({'error': 'Username dan password wajib diisi!'}), 400
        
    if User.query.filter_by(username=username).first():
        return jsonify({'error': 'Username sudah digunakan!'}), 400
        
    new_user = User(username=username, password=password, role='client')
    db.session.add(new_user)
    db.session.commit()
    
    return jsonify({'status': 'success', 'message': 'Anggota berhasil ditambahkan!'})

@app.route('/api/members/edit/<int:user_id>', methods=['POST'])
def edit_member(user_id):
    if session.get('role') != 'admin':
        return jsonify({'error': 'Unauthorized'}), 401
        
    user = User.query.get(user_id)
    if not user:
        return jsonify({'error': 'User tidak ditemukan'}), 404
        
    data = request.json
    new_username = data.get('username')
    new_password = data.get('password')
    new_role = data.get('role')
    
    # Validasi username ganti ke milik orang lain
    existing = User.query.filter_by(username=new_username).first()
    if existing and existing.id != user.id:
        return jsonify({'error': 'Username sudah digunakan oleh akun lain!'}), 400
        
    user.username = new_username
    user.role = new_role
    
    # Jika password diisi, ubah password
    if new_password and new_password.strip():
        user.password = new_password
        
    db.session.commit()
    return jsonify({'status': 'success', 'message': 'Data anggota berhasil diperbarui!'})

@app.route('/api/members/delete/<int:user_id>', methods=['POST'])
def delete_member(user_id):
    if session.get('role') != 'admin':
        return jsonify({'error': 'Unauthorized'}), 401
        
    user = User.query.get(user_id)
    if user and user.role == 'client':
        # Hapus juga semua riwayat nilai siswa ini (opsional agar DB bersih)
        Score.query.filter_by(user_id=user.id).delete()
        
        db.session.delete(user)
        db.session.commit()
        return jsonify({'status': 'success', 'message': 'Anggota dihapus!'})
        
    return jsonify({'error': 'User tidak ditemukan'}), 404

@app.route('/api/members/reset_task/<int:user_id>', methods=['POST'])
def reset_member_task(user_id):
    if session.get('role') != 'admin':
        return jsonify({'error': 'Unauthorized'}), 401
        
    user = User.query.get(user_id)
    if user and user.role == 'client':
        # Fitur ini berguna jika siswa ingin mengulang tugas, admin tinggal mereset status tugasnya
        user.task_title = None
        user.task_description = None
        user.task_paths = None
        db.session.commit()
        return jsonify({'status': 'success', 'message': 'Tugas anggota direset!'})
        
    return jsonify({'error': 'User tidak ditemukan'}), 404


@app.route('/api/members/reset_all_tasks', methods=['POST'])
def reset_all_members_tasks():
    if session.get('role') != 'admin':
        return jsonify({'error': 'Unauthorized'}), 401

    # Reset task_title, task_description, dan task_paths untuk semua user dengan role 'client'
    User.query.filter_by(role='client').update({
        'task_title': None,
        'task_description': None,
        'task_paths': None
    })
    db.session.commit()

    return jsonify({'status': 'success', 'message': 'Semua tugas siswa berhasil direset!'})

@app.route('/api/profile/edit', methods=['POST'])
def edit_my_profile():
    if not session.get('user_id'):
        return jsonify({'error': 'Unauthorized'}), 401
        
    user = User.query.get(session['user_id'])
    if not user:
        return jsonify({'error': 'User tidak ditemukan'}), 404
        
    data = request.json
    new_username = data.get('username')
    new_password = data.get('password')
    
    if not new_username:
        return jsonify({'error': 'Username tidak boleh kosong!'}), 400
        
    # Cek jika username diganti dan sudah dipakai orang lain
    existing = User.query.filter_by(username=new_username).first()
    if existing and existing.id != user.id:
        return jsonify({'error': 'Username sudah digunakan oleh akun lain!'}), 400
        
    user.username = new_username
    session['username'] = new_username  # Perbarui session username
    
    if new_password and new_password.strip():
        user.password = new_password
        
    db.session.commit()
    return jsonify({'status': 'success', 'message': 'Profil berhasil diperbarui!'})

# --- WEBSOCKETS ---
@socketio.on('join')
def on_join(data):
    if session.get('role') == 'admin':
        join_room('admin_room')
        print(f"Admin {session['username']} joined admin_room")

if __name__ == '__main__':
    socketio.run(app, debug=True, port=8004)