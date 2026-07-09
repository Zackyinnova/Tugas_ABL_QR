from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file, jsonify
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from models import db, User, Mahasiswa, Dosen, SesiAbsensi, Absensi, Kelas
from datetime import datetime, date, time, timedelta
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
db_url = "mysql+pymysql://root:@localhost/db_abl_absensi"

# Kalau deploy (Railway/hosting lain), ambil dari environment variable
db_url = os.environ.get('DATABASE_URL', db_url)
if db_url.startswith('mysql://'):
    db_url = db_url.replace('mysql://', 'mysql+pymysql://', 1)

app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)

QR_FOLDER = os.path.join('static', 'qr')
os.makedirs(QR_FOLDER, exist_ok=True)

# Batas waktu telat (dalam menit) dihitung dari jam_mulai sesi
BATAS_TELAT_MENIT = 60


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
# DOSEN - DASHBOARD & SESI
# ─────────────────────────────────────────────

@app.route('/dosen/dashboard')
@login_required('dosen')
def dashboard_dosen():
    dosen = Dosen.query.filter_by(user_id=session['user_id']).first()
    sesi_list = SesiAbsensi.query.filter_by(dosen_id=dosen.id).order_by(
        SesiAbsensi.tanggal.desc(), SesiAbsensi.jam_mulai.desc()
    ).all()

    stats = {}
    for sesi in sesi_list:
        stats[sesi.id] = {
            'total': len(sesi.absensi),
            'hadir': sum(1 for a in sesi.absensi if a.status == 'hadir'),
            'telat': sum(1 for a in sesi.absensi if a.status == 'telat'),
            'izin': sum(1 for a in sesi.absensi if a.status == 'izin'),
            'tidak_masuk': sum(1 for a in sesi.absensi if a.status == 'tidak_masuk'),
        }

    return render_template('dashboard_dosen.html', sesi_list=sesi_list, stats=stats, dosen=dosen)


@app.route('/dosen/buat-sesi', methods=['GET', 'POST'])
@login_required('dosen')
def buat_sesi():
    dosen = Dosen.query.filter_by(user_id=session['user_id']).first()
    kelas_list = Kelas.query.filter_by(dosen_id=dosen.id).all()

    if request.method == 'POST':
        matakuliah = request.form.get('matakuliah', '').strip()
        pertemuan_ke = request.form.get('pertemuan_ke', 1)
        tanggal_str = request.form.get('tanggal', '')
        jam_mulai_str = request.form.get('jam_mulai', '')
        jam_selesai_str = request.form.get('jam_selesai', '')
        kelas_id = request.form.get('kelas_id', '')

        if not all([matakuliah, pertemuan_ke, tanggal_str, jam_mulai_str, jam_selesai_str, kelas_id]):
            flash('Semua field harus diisi, termasuk kelas.', 'danger')
            return render_template('buat_sesi.html', kelas_list=kelas_list)

        try:
            tanggal = datetime.strptime(tanggal_str, '%Y-%m-%d').date()
            jam_mulai = datetime.strptime(jam_mulai_str, '%H:%M').time()
            jam_selesai = datetime.strptime(jam_selesai_str, '%H:%M').time()
        except ValueError:
            flash('Format tanggal atau jam tidak valid.', 'danger')
            return render_template('buat_sesi.html', kelas_list=kelas_list)

        kelas = Kelas.query.filter_by(id=kelas_id, dosen_id=dosen.id).first()
        if not kelas:
            flash('Kelas tidak valid.', 'danger')
            return render_template('buat_sesi.html', kelas_list=kelas_list)

        token = str(uuid.uuid4())
        sesi = SesiAbsensi(
            matakuliah=matakuliah,
            pertemuan_ke=int(pertemuan_ke),
            tanggal=tanggal,
            jam_mulai=jam_mulai,
            jam_selesai=jam_selesai,
            token=token,
            dosen_id=dosen.id,
            kelas_id=kelas.id
        )
        db.session.add(sesi)
        db.session.flush()  # supaya sesi.id sudah terbentuk sebelum dipakai di bawah

        # Generate baris absensi untuk SEMUA mahasiswa yang terdaftar di kelas ini
        for mhs in kelas.mahasiswa_list:
            absensi = Absensi(
                mahasiswa_id=mhs.id,
                sesi_id=sesi.id,
                status='tidak_masuk'
            )
            db.session.add(absensi)

        db.session.commit()

        # Generate QR
        generate_qr(token, sesi.id)

        flash(f'Sesi berhasil dibuat untuk {matakuliah} ({kelas.nama_kelas})!', 'success')
        return redirect(url_for('detail_sesi', sesi_id=sesi.id))

    return render_template('buat_sesi.html', kelas_list=kelas_list)


@app.route('/dosen/sesi/<int:sesi_id>')
@login_required('dosen')
def detail_sesi(sesi_id):
    dosen = Dosen.query.filter_by(user_id=session['user_id']).first()
    sesi = SesiAbsensi.query.filter_by(id=sesi_id, dosen_id=dosen.id).first_or_404()
    absensi_list = Absensi.query.filter_by(sesi_id=sesi_id).join(Mahasiswa).order_by(Mahasiswa.nama).all()

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


@app.route('/dosen/sesi/<int:sesi_id>/ubah-status/<int:mahasiswa_id>', methods=['POST'])
@login_required('dosen')
def ubah_status(sesi_id, mahasiswa_id):
    dosen = Dosen.query.filter_by(user_id=session['user_id']).first()
    sesi = SesiAbsensi.query.filter_by(id=sesi_id, dosen_id=dosen.id).first_or_404()

    status_baru = request.form.get('status')
    if status_baru not in ['hadir', 'telat', 'izin', 'tidak_masuk']:
        flash('Status tidak valid.', 'danger')
        return redirect(url_for('detail_sesi', sesi_id=sesi_id))

    absensi = Absensi.query.filter_by(sesi_id=sesi_id, mahasiswa_id=mahasiswa_id).first_or_404()
    absensi.status = status_baru
    if status_baru in ['hadir', 'telat'] and not absensi.waktu_absen:
        absensi.waktu_absen = datetime.now()
    db.session.commit()

    flash('Status berhasil diubah.', 'success')
    return redirect(url_for('detail_sesi', sesi_id=sesi_id))


@app.route('/dosen/sesi/<int:sesi_id>/export')
@login_required('dosen')
def export_csv(sesi_id):
    dosen = Dosen.query.filter_by(user_id=session['user_id']).first()
    sesi = SesiAbsensi.query.filter_by(id=sesi_id, dosen_id=dosen.id).first_or_404()
    absensi_list = Absensi.query.filter_by(sesi_id=sesi_id).join(Mahasiswa).order_by(Mahasiswa.nama).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['No', 'NIM', 'Nama', 'Status', 'Waktu Absen'])
    for i, ab in enumerate(absensi_list, 1):
        writer.writerow([
            i,
            ab.mahasiswa.nim,
            ab.mahasiswa.nama,
            ab.status,
            ab.waktu_absen.strftime('%Y-%m-%d %H:%M:%S') if ab.waktu_absen else '-'
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
# DOSEN - KELOLA KELAS
# ─────────────────────────────────────────────

@app.route('/dosen/kelas')
@login_required('dosen')
def list_kelas():
    dosen = Dosen.query.filter_by(user_id=session['user_id']).first()
    kelas_list = Kelas.query.filter_by(dosen_id=dosen.id).all()
    return render_template('list_kelas.html', kelas_list=kelas_list)


@app.route('/dosen/kelas/tambah', methods=['GET', 'POST'])
@login_required('dosen')
def tambah_kelas():
    dosen = Dosen.query.filter_by(user_id=session['user_id']).first()
    if request.method == 'POST':
        nama_kelas = request.form.get('nama_kelas', '').strip()
        if not nama_kelas:
            flash('Nama kelas harus diisi.', 'danger')
            return render_template('tambah_kelas.html')

        kelas = Kelas(nama_kelas=nama_kelas, dosen_id=dosen.id)
        db.session.add(kelas)
        db.session.commit()
        flash(f'Kelas "{nama_kelas}" berhasil dibuat.', 'success')
        return redirect(url_for('detail_kelas', kelas_id=kelas.id))

    return render_template('tambah_kelas.html')


@app.route('/dosen/kelas/<int:kelas_id>')
@login_required('dosen')
def detail_kelas(kelas_id):
    dosen = Dosen.query.filter_by(user_id=session['user_id']).first()
    kelas = Kelas.query.filter_by(id=kelas_id, dosen_id=dosen.id).first_or_404()

    # Mahasiswa yang belum ada di kelas ini, buat opsi tambah
    id_terdaftar = [m.id for m in kelas.mahasiswa_list]
    mahasiswa_belum_ikut = Mahasiswa.query.filter(~Mahasiswa.id.in_(id_terdaftar)).all() if id_terdaftar else Mahasiswa.query.all()

    return render_template('detail_kelas.html', kelas=kelas, mahasiswa_belum_ikut=mahasiswa_belum_ikut)


@app.route('/dosen/kelas/<int:kelas_id>/tambah-mahasiswa', methods=['POST'])
@login_required('dosen')
def tambah_mahasiswa_kelas(kelas_id):
    dosen = Dosen.query.filter_by(user_id=session['user_id']).first()
    kelas = Kelas.query.filter_by(id=kelas_id, dosen_id=dosen.id).first_or_404()

    mahasiswa_id = request.form.get('mahasiswa_id')
    mhs = Mahasiswa.query.get(mahasiswa_id)
    if mhs and mhs not in kelas.mahasiswa_list:
        kelas.mahasiswa_list.append(mhs)
        db.session.commit()
        flash(f'{mhs.nama} berhasil ditambahkan ke kelas.', 'success')
    else:
        flash('Mahasiswa tidak valid atau sudah terdaftar.', 'warning')

    return redirect(url_for('detail_kelas', kelas_id=kelas_id))


@app.route('/dosen/kelas/<int:kelas_id>/hapus-mahasiswa/<int:mahasiswa_id>', methods=['POST'])
@login_required('dosen')
def hapus_mahasiswa_kelas(kelas_id, mahasiswa_id):
    dosen = Dosen.query.filter_by(user_id=session['user_id']).first()
    kelas = Kelas.query.filter_by(id=kelas_id, dosen_id=dosen.id).first_or_404()

    mhs = Mahasiswa.query.get(mahasiswa_id)
    if mhs and mhs in kelas.mahasiswa_list:
        kelas.mahasiswa_list.remove(mhs)
        db.session.commit()
        flash(f'{mhs.nama} dikeluarkan dari kelas.', 'success')

    return redirect(url_for('detail_kelas', kelas_id=kelas_id))


# ─────────────────────────────────────────────
# MAHASISWA
# ─────────────────────────────────────────────

@app.route('/mahasiswa/dashboard')
@login_required('mahasiswa')
def dashboard_mahasiswa():
    mhs = Mahasiswa.query.filter_by(user_id=session['user_id']).first()
    absensi_list = Absensi.query.filter_by(mahasiswa_id=mhs.id).order_by(Absensi.id.desc()).all()
    return render_template('dashboard_mahasiswa.html', mahasiswa=mhs, absensi_list=absensi_list)


# ─────────────────────────────────────────────
# QR SCAN
# ─────────────────────────────────────────────

@app.route('/scan/<token>')
def scan_qr(token):
    if 'user_id' not in session:
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

    sesi_start = datetime.combine(sesi.tanggal, sesi.jam_mulai)
    sesi_end = datetime.combine(sesi.tanggal, sesi.jam_selesai)

    # Cek sesi sudah berakhir
    if now > sesi_end:
        return render_template('scan_result.html',
                               status='expired',
                               message=f'Sesi absensi telah berakhir pada {sesi_end.strftime("%H:%M")}.',
                               sesi=sesi)

    # Cek tanggal sesuai hari ini
    if sesi.tanggal != date.today():
        return render_template('scan_result.html',
                               status='error',
                               message=f'QR Code ini hanya valid untuk tanggal {sesi.tanggal.strftime("%d/%m/%Y")}.',
                               sesi=sesi)

    # Cari baris absensi yang sudah ter-generate otomatis saat sesi dibuat
    absensi = Absensi.query.filter_by(mahasiswa_id=mhs.id, sesi_id=sesi.id).first()

    if not absensi:
        return render_template('scan_result.html',
                               status='error',
                               message='Anda tidak terdaftar di kelas untuk sesi ini.',
                               sesi=sesi)

    # Cek duplikat (sudah tercatat statusnya selain tidak_masuk)
    if absensi.status != 'tidak_masuk':
        return render_template('scan_result.html',
                               status='duplicate',
                               message=f'Anda sudah tercatat dengan status "{absensi.status.upper()}" pada sesi ini.',
                               sesi=sesi,
                               waktu=absensi.waktu_absen)

    # Tentukan status: hadir atau telat berdasarkan selisih dari jam_mulai
    batas_waktu = sesi_start + timedelta(minutes=BATAS_TELAT_MENIT)
    if now > batas_waktu:
        absensi.status = 'telat'
    else:
        absensi.status = 'hadir'

    absensi.waktu_absen = now
    db.session.commit()

    return render_template('scan_result.html',
                           status='success',
                           message=f'Absensi berhasil dicatat dengan status: {absensi.status.upper()}!',
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
    db.session.flush()

    # Kelas contoh
    kelas = Kelas(nama_kelas='TI-3A', dosen_id=dosen.id)
    db.session.add(kelas)
    db.session.flush()

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
        db.session.flush()
        kelas.mahasiswa_list.append(mhs)

    db.session.commit()
    print("Data dummy berhasil dibuat!")
    print("   Dosen     -> username: dosen1   | password: dosen123")
    print("   Kelas     -> TI-3A (5 mahasiswa terdaftar)")
    print("   Mahasiswa -> username: [NIM]    | password: mhs123")
    print("   NIM: 2201001 s/d 2201005")


with app.app_context():
    db.create_all()
    seed_data()


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)