from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()

class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False)  # 'dosen' atau 'mahasiswa'

    mahasiswa = db.relationship('Mahasiswa', backref='user', uselist=False)
    dosen = db.relationship('Dosen', backref='user', uselist=False)


class Mahasiswa(db.Model):
    __tablename__ = 'mahasiswa'
    id = db.Column(db.Integer, primary_key=True)
    nim = db.Column(db.String(20), unique=True, nullable=False)
    nama = db.Column(db.String(100), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)

    absensi = db.relationship('Absensi', backref='mahasiswa', lazy=True)


class Dosen(db.Model):
    __tablename__ = 'dosen'
    id = db.Column(db.Integer, primary_key=True)
    nama = db.Column(db.String(100), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)


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

    absensi = db.relationship('Absensi', backref='sesi', lazy=True)
    dosen = db.relationship('Dosen', backref='sesi_list')


class Absensi(db.Model):
    __tablename__ = 'absensi'
    id = db.Column(db.Integer, primary_key=True)
    mahasiswa_id = db.Column(db.Integer, db.ForeignKey('mahasiswa.id'), nullable=False)
    sesi_id = db.Column(db.Integer, db.ForeignKey('sesi_absensi.id'), nullable=False)
    waktu_absen = db.Column(db.DateTime, default=datetime.now)
