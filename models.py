from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()

# Tabel penghubung many-to-many antara mahasiswa dan kelas
mahasiswa_kelas = db.Table('mahasiswa_kelas',
    db.Column('id', db.Integer, primary_key=True),
    db.Column('mahasiswa_id', db.Integer, db.ForeignKey('mahasiswa.id'), nullable=False),
    db.Column('kelas_id', db.Integer, db.ForeignKey('kelas.id'), nullable=False)
)


class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False)

    mahasiswa = db.relationship('Mahasiswa', backref='user', uselist=False)
    dosen = db.relationship('Dosen', backref='user', uselist=False)


class Mahasiswa(db.Model):
    __tablename__ = 'mahasiswa'
    id = db.Column(db.Integer, primary_key=True)
    nim = db.Column(db.String(20), unique=True, nullable=False)
    nama = db.Column(db.String(100), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)

    absensi = db.relationship('Absensi', backref='mahasiswa', lazy=True)
    kelas_list = db.relationship('Kelas', secondary=mahasiswa_kelas, backref='mahasiswa_list')


class Dosen(db.Model):
    __tablename__ = 'dosen'
    id = db.Column(db.Integer, primary_key=True)
    nama = db.Column(db.String(100), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)


class Kelas(db.Model):
    __tablename__ = 'kelas'
    id = db.Column(db.Integer, primary_key=True)
    nama_kelas = db.Column(db.String(50), nullable=False)
    dosen_id = db.Column(db.Integer, db.ForeignKey('dosen.id'), nullable=False)

    sesi_list = db.relationship('SesiAbsensi', backref='kelas', lazy=True)


class SesiAbsensi(db.Model):
    __tablename__ = 'sesi_absensi'
    id = db.Column(db.Integer, primary_key=True)
    matakuliah = db.Column(db.String(100), nullable=False)
    pertemuan_ke = db.Column(db.Integer, nullable=False)
    tanggal = db.Column(db.Date, nullable=False)
    jam_mulai = db.Column(db.Time, nullable=False)
    jam_selesai = db.Column(db.Time, nullable=False)
    token = db.Column(db.String(36), unique=True, nullable=False)
    dosen_id = db.Column(db.Integer, db.ForeignKey('dosen.id'), nullable=False)
    kelas_id = db.Column(db.Integer, db.ForeignKey('kelas.id'), nullable=False)
    batas_telat_menit = db.Column(db.Integer, nullable=False, default=30)  # <-- baris baru

    absensi = db.relationship('Absensi', backref='sesi', lazy=True)
    dosen = db.relationship('Dosen', backref='sesi_list')


class Absensi(db.Model):
    __tablename__ = 'absensi'
    id = db.Column(db.Integer, primary_key=True)
    mahasiswa_id = db.Column(db.Integer, db.ForeignKey('mahasiswa.id'), nullable=False)
    sesi_id = db.Column(db.Integer, db.ForeignKey('sesi_absensi.id'), nullable=False)
    waktu_absen = db.Column(db.DateTime, nullable=True)
    status = db.Column(
        db.Enum('hadir', 'telat', 'izin', 'tidak_masuk', name='status_absensi'),
        nullable=False,
        default='tidak_masuk'
    )