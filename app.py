from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file, jsonify
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from models import db, User, Mahasiswa, Dosen, SesiAbsensi, Absensi
from datetime import datetime, date, time
import uuid
import qrcode
import os
import csv
import io
import pymysql

pymysql.install_as_MySQLdb()

app = Flask(__name__)
app.secret_key = 'absensi_qr_secret_key_2024'

# Lokal
db_url = 'mysql+pymysql://root:@localhost/db_abl_absensi'

# Kalau deploy (Railway/hosting lain), ambil dari environment variable
db_url = os.environ.get('DATABASE_URL', db_url)
if db_url.startswith('mysql://'):
    db_url = db_url.replace('mysql://', 'mysql+pymysql://', 1)

app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)

QR_FOLDER = os.path.join('static', 'qr')
os.makedirs(QR_FOLDER, exist_ok=True)

@app.context_processor
def inject_globals():
    now = datetime.now()
    return {
        'now_date': now.strftime('%Y-%m-%d'),
        'now_time': now.strftime('%H:%M'),
        'now_datetime': now,
    }

# ─────────────────────────────────────────────
# HELPER
# ─────────────────────────────────────────────

def login_required(role=None):
    def decorator(f):
        from functools import wraps
        @wraps(f)
        def decorated(*args, **kwargs):
            if 'user_id' not in session:
                flash('Silakan login terlebih dahulu.', 'warning')
                return redirect(url_for('login'))
            if role and session.get('role') != role:
                flash('Akses ditolak.', 'danger')
                return redirect(url_for('login'))
            return f(*args, **kwargs)
        return decorated
    return decorator


def generate_qr(token, sesi_id):
    url = f"/scan/{token}"
    full_url = request.host_url.rstrip('/') + url
    img = qrcode.make(full_url)
    path = os.path.join(QR_FOLDER, f"qr_{sesi_id}.png")
    img.save(path)
    return path


# ─────────────────────────────────────────────
# AUTH
# ─────────────────────────────────────────────

@app.route('/')
def index():
    if 'user_id' in session:
        if session['role'] == 'dosen':
            return redirect(url_for('dashboard_dosen'))
        return redirect(url_for('dashboard_mahasiswa'))
    return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session:
        return redirect(url_for('index'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password, password):
            session['user_id'] = user.id
            session['role'] = user.role
            session['username'] = user.username
            if user.role == 'dosen':
                session['nama'] = user.dosen.nama if user.dosen else username
                return redirect(url_for('dashboard_dosen'))
            else:
                mhs = user.mahasiswa
                session['nama'] = mhs.nama if mhs else username
                session['nim'] = mhs.nim if mhs else ''
                next_url = session.pop('next', None)
                if next_url:
                    return redirect(next_url)
                return redirect(url_for('dashboard_mahasiswa'))
        flash('Username atau password salah.', 'danger')
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    flash('Berhasil logout.', 'success')
    return redirect(url_for('login'))


# ─────────────────────────────────────────────
# DOSEN
# ─────────────────────────────────────────────

@app.route('/dosen/dashboard')
@login_required('dosen')
def dashboard_dosen():
    dosen = Dosen.query.filter_by(user_id=session['user_id']).first()
    sesi_list = SesiAbsensi.query.filter_by(dosen_id=dosen.id).order_by(SesiAbsensi.tanggal.desc(), SesiAbsensi.jam_mulai.desc()).all()
    
    stats = {}
    for sesi in sesi_list:
        stats[sesi.id] = len(sesi.absensi)
    
    return render_template('dashboard_dosen.html', sesi_list=sesi_list, stats=stats, dosen=dosen)


@app.route('/dosen/buat-sesi', methods=['GET', 'POST'])
@login_required('dosen')
def buat_sesi():
    dosen = Dosen.query.filter_by(user_id=session['user_id']).first()
    if request.method == 'POST':
        matakuliah = request.form.get('matakuliah', '').strip()
        pertemuan_ke = request.form.get('pertemuan_ke', 1)
        tanggal_str = request.form.get('tanggal', '')
        jam_mulai_str = request.form.get('jam_mulai', '')
        jam_selesai_str = request.form.get('jam_selesai', '')

        if not all([matakuliah, pertemuan_ke, tanggal_str, jam_mulai_str, jam_selesai_str]):
            flash('Semua field harus diisi.', 'danger')
            return render_template('buat_sesi.html')

        try:
            tanggal = datetime.strptime(tanggal_str, '%Y-%m-%d').date()
            jam_mulai = datetime.strptime(jam_mulai_str, '%H:%M').time()
            jam_selesai = datetime.strptime(jam_selesai_str, '%H:%M').time()
        except ValueError:
            flash('Format tanggal atau jam tidak valid.', 'danger')
            return render_template('buat_sesi.html')

        token = str(uuid.uuid4())
        sesi = SesiAbsensi(
            matakuliah=matakuliah,
            pertemuan_ke=int(pertemuan_ke),
            tanggal=tanggal,
            jam_mulai=jam_mulai,
            jam_selesai=jam_selesai,
            token=token,
            dosen_id=dosen.id
        )
        db.session.add(sesi)
        db.session.commit()

        # Generate QR
        generate_qr(token, sesi.id)

        flash(f'Sesi berhasil dibuat untuk {matakuliah}!', 'success')
        return redirect(url_for('detail_sesi', sesi_id=sesi.id))

    return render_template('buat_sesi.html')


@app.route('/dosen/sesi/<int:sesi_id>')
@login_required('dosen')
def detail_sesi(sesi_id):
    dosen = Dosen.query.filter_by(user_id=session['user_id']).first()
    sesi = SesiAbsensi.query.filter_by(id=sesi_id, dosen_id=dosen.id).first_or_404()
    absensi_list = Absensi.query.filter_by(sesi_id=sesi_id).order_by(Absensi.waktu_absen).all()
    
    qr_path = f"qr/qr_{sesi.id}.png"
    qr_exists = os.path.exists(os.path.join('static', qr_path))

    # Regenerate jika belum ada
    if not qr_exists:
        generate_qr(sesi.token, sesi.id)
        qr_exists = True

    now = datetime.now()
    sesi_datetime_end = datetime.combine(sesi.tanggal, sesi.jam_selesai)
    is_active = now <= sesi_datetime_end and sesi.tanggal == date.today()

    return render_template('detail_sesi.html', sesi=sesi, absensi_list=absensi_list, 
                           qr_path=qr_path, qr_exists=qr_exists, is_active=is_active)


@app.route('/dosen/sesi/<int:sesi_id>/export')
@login_required('dosen')
def export_csv(sesi_id):
    dosen = Dosen.query.filter_by(user_id=session['user_id']).first()
    sesi = SesiAbsensi.query.filter_by(id=sesi_id, dosen_id=dosen.id).first_or_404()
    absensi_list = Absensi.query.filter_by(sesi_id=sesi_id).order_by(Absensi.waktu_absen).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['No', 'NIM', 'Nama', 'Waktu Absen'])
    for i, ab in enumerate(absensi_list, 1):
        writer.writerow([
            i,
            ab.mahasiswa.nim,
            ab.mahasiswa.nama,
            ab.waktu_absen.strftime('%Y-%m-%d %H:%M:%S')
        ])

    output.seek(0)
    filename = f"absensi_{sesi.matakuliah}_pertemuan{sesi.pertemuan_ke}_{sesi.tanggal}.csv"
    return send_file(
        io.BytesIO(output.getvalue().encode('utf-8-sig')),
        mimetype='text/csv',
        as_attachment=True,
        download_name=filename
    )


@app.route('/dosen/sesi/<int:sesi_id>/regenerate-qr')
@login_required('dosen')
def regenerate_qr(sesi_id):
    dosen = Dosen.query.filter_by(user_id=session['user_id']).first()
    sesi = SesiAbsensi.query.filter_by(id=sesi_id, dosen_id=dosen.id).first_or_404()
    generate_qr(sesi.token, sesi.id)
    flash('QR Code berhasil di-generate ulang.', 'success')
    return redirect(url_for('detail_sesi', sesi_id=sesi_id))


# ─────────────────────────────────────────────
# MAHASISWA
# ─────────────────────────────────────────────

@app.route('/mahasiswa/dashboard')
@login_required('mahasiswa')
def dashboard_mahasiswa():
    mhs = Mahasiswa.query.filter_by(user_id=session['user_id']).first()
    absensi_list = Absensi.query.filter_by(mahasiswa_id=mhs.id).order_by(Absensi.waktu_absen.desc()).all()
    return render_template('dashboard_mahasiswa.html', mahasiswa=mhs, absensi_list=absensi_list)


# ─────────────────────────────────────────────
# QR SCAN
# ─────────────────────────────────────────────

@app.route('/scan/<token>')
def scan_qr(token):
    if 'user_id' not in session:
        # Simpan redirect target ke session
        session['next'] = url_for('scan_qr', token=token)
        flash('Silakan login terlebih dahulu untuk absen.', 'warning')
        return redirect(url_for('login'))
    
    if session.get('role') != 'mahasiswa':
        flash('Hanya mahasiswa yang dapat melakukan absensi.', 'danger')
        return redirect(url_for('dashboard_dosen'))

    sesi = SesiAbsensi.query.filter_by(token=token).first()
    if not sesi:
        return render_template('scan_result.html', 
                               status='error', 
                               message='Sesi tidak ditemukan. QR Code tidak valid.')

    mhs = Mahasiswa.query.filter_by(user_id=session['user_id']).first()
    now = datetime.now()

    # Cek tanggal dan jam selesai
    sesi_end = datetime.combine(sesi.tanggal, sesi.jam_selesai)
    sesi_start = datetime.combine(sesi.tanggal, sesi.jam_mulai)

    if now > sesi_end:
        return render_template('scan_result.html',
                               status='expired',
                               message=f'Sesi absensi telah berakhir pada {sesi_end.strftime("%H:%M")}.',
                               sesi=sesi)

    if sesi.tanggal != date.today():
        return render_template('scan_result.html',
                               status='error',
                               message=f'QR Code ini hanya valid untuk tanggal {sesi.tanggal.strftime("%d/%m/%Y")}.',
                               sesi=sesi)

    # Cek duplikat
    existing = Absensi.query.filter_by(mahasiswa_id=mhs.id, sesi_id=sesi.id).first()
    if existing:
        return render_template('scan_result.html',
                               status='duplicate',
                               message='Anda sudah melakukan absensi pada sesi ini.',
                               sesi=sesi,
                               waktu=existing.waktu_absen)

    # Catat absensi
    absensi = Absensi(
        mahasiswa_id=mhs.id,
        sesi_id=sesi.id,
        waktu_absen=now
    )
    db.session.add(absensi)
    db.session.commit()

    return render_template('scan_result.html',
                           status='success',
                           message='Absensi berhasil dicatat!',
                           sesi=sesi,
                           mahasiswa=mhs,
                           waktu=now)


@app.route('/scanner')
@login_required('mahasiswa')
def scanner():
    return render_template('scanner.html')


# ─────────────────────────────────────────────
# INIT DB & SEED
# ─────────────────────────────────────────────

def seed_data():
    if User.query.first():
        return  # Sudah ada data

    # Dosen
    dosen_user = User(
        username='dosen1',
        password=generate_password_hash('dosen123'),
        role='dosen'
    )
    db.session.add(dosen_user)
    db.session.flush()

    dosen = Dosen(nama='Dr. Budi Santoso, M.Kom', user_id=dosen_user.id)
    db.session.add(dosen)

    # Mahasiswa
    mahasiswa_data = [
        ('2201001', 'Andi Pratama'),
        ('2201002', 'Bela Sari'),
        ('2201003', 'Cahyo Nugroho'),
        ('2201004', 'Dewi Rahayu'),
        ('2201005', 'Eko Firmansyah'),
    ]

    for nim, nama in mahasiswa_data:
        mhs_user = User(
            username=nim,
            password=generate_password_hash('mhs123'),
            role='mahasiswa'
        )
        db.session.add(mhs_user)
        db.session.flush()
        mhs = Mahasiswa(nim=nim, nama=nama, user_id=mhs_user.id)
        db.session.add(mhs)

    db.session.commit()
    print("✅ Data dummy berhasil dibuat!")
    print("   Dosen   → username: dosen1   | password: dosen123")
    print("   Mahasiswa → username: [NIM]  | password: mhs123")
    print("   NIM: 2201001 s/d 2201005")


if __name__ == "__main__":
    app.run(debug=True, host='0.0.0.0', port=5000)