# -*- coding: utf-8 -*-
"""
Qarz Nazorat — Web/Electron versiyasi uchun API server.
Bu server mavjud database.py, letters.py, importer.py, util.py
modullarini o'zgartirmasdan qayta ishlatadi — faqat ularga HTTP
orqali kirish imkonini beradi.
"""
import os
import sys
import datetime
from flask import Flask, jsonify, request
from flask_cors import CORS

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import database as db
import util
import letters
import importer

app = Flask(__name__)
CORS(app)

db.init_db()


def mustahkam_fayl_saqlash(f, tmp_path, ozbek_kengaytma_tekshiruvi=True):
    """Yuklangan faylni (Excel/Word) diskka MUSTAHKAM usulda yozadi: to'liq
    xotiraga o'qib, binary rejimda flush+fsync bilan saqlaydi. Bu ba'zi
    Windows muhitlarida (masalan antivirus fayl yozilayotgan paytda
    tekshirsa) faylning yarim yozilgan holda o'qilib qolishi ("incorrect
    header check" kabi tushunarsiz xatolar) ehtimolini kamaytiradi.
    Agar fayl .xlsx/.xlsb/.docx bo'lib, lekin ZIP tuzilishiga mos kelmasa
    (PK bilan boshlanmasa), tushunarli xato qaytaradi. ZIP-tekshiruvi
    yoqilmagan hollarda (masalan .txt, .pdf) hajm chegarasi qo'llanmaydi —
    kichik matn/hujjat fayllari ham haqiqiy va yaroqli bo'lishi mumkin.
    Muvaffaqiyatli bo'lsa None, aks holda (xabar, http_kod) qaytaradi."""
    baytlar = f.read()
    if len(baytlar) == 0:
        return ("Yuklangan fayl bo'sh — qayta urinib ko'ring.", 400)
    if ozbek_kengaytma_tekshiruvi:
        if len(baytlar) < 100:
            return ("Yuklangan fayl juda kichik — qayta urinib ko'ring.", 400)
        if baytlar[:2] != b'PK':
            return ("Fayl to'liq yuklanmadi yoki buzilgan (ZIP formatiga mos emas). "
                     "Iltimos, faylni qaytadan tanlab, qayta urinib ko'ring.", 400)
    with open(tmp_path, 'wb') as out:
        out.write(baytlar)
        out.flush()
        os.fsync(out.fileno())
    return None

AMAL_TURLARI_MAP = {
    'oylik_ish_haqqi': "Oylik ish haqqiga qaratildi",
    'avto_taqiq': "Avto transportga taqiq qo'yildi",
    'avto_qidiruv': "Avto transport qidiruvga berildi",
    'chetga_chiqish_taqiq': "Chetga chiqishga taqiq qo'yilgan",
    'majburiy_xatlov': "Majburiy xatlov o'tkazildi",
    'sotish_togridan': "To'g'ridan-to'g'ri sotildi",
    'sotish_auksion': "Auksion yo'li bilan sotildi",
    'kafil_ish': "Kafil bo'yicha ish qilindi",
    'garov_xatlov': "Garov mulkiga xatlov o'tkazildi",
    'garov_sotish': "Garov mulki sotildi",
    'eski_ish_kiritildi': "Eski ish sifatida bazaga kiritildi",
    'ish_haqiga_qaratish': "Oylik ish haqqiga qaratildi",
}


def hujjatlar_papkasi():
    """Yaratilgan hujjatlar (xat, Davo ariza, MIB, sug'urta) saqlanadigan asosiy
    papka. Sozlamalarda ko'rsatilgan bo'lsa o'sha joy, aks holda standart papka
    (dastur ishga tushirilgan joy) ishlatiladi. Shu bilan foydalanuvchi
    hujjatlarni istalgan diskda (masalan D:\\) saqlashi mumkin."""
    sozlama = db.get_all_settings().get('hujjatlar_papkasi', '').strip()
    papka = sozlama if sozlama else os.path.join(db._app_dir(), 'yaratilgan_xatlar')
    os.makedirs(papka, exist_ok=True)
    return papka


def mib_hujjatlar_papkasi():
    sozlama = db.get_all_settings().get('hujjatlar_papkasi', '').strip()
    papka = os.path.join(sozlama, 'mib_hujjatlar') if sozlama else os.path.join(db._app_dir(), 'mib_hujjatlar')
    os.makedirs(papka, exist_ok=True)
    return papka


def row_list(rows):
    """sqlite3.Row ro'yxatini JSON-serializable dict ro'yxatiga aylantiradi."""
    return [dict(r) for r in rows] if rows else []


# ---------------------------------------------------------------------------
# AUTENTIFIKATSIYA
# ---------------------------------------------------------------------------
@app.route('/api/auth/status', methods=['GET'])
def auth_status():
    return jsonify({'parol_kerak': db.parol_ornatilganmi()})


@app.route('/api/auth/login', methods=['POST'])
def auth_login():
    data = request.get_json() or {}
    parol = data.get('parol', '')
    ok = db.parol_tekshirish(parol)
    return jsonify({'ok': ok})


@app.route('/api/sozlamalar', methods=['GET'])
def sozlamalar_get():
    return jsonify(db.get_all_settings())


@app.route('/api/sozlamalar', methods=['POST'])
def sozlamalar_save():
    data = request.get_json() or {}
    for kalit, qiymat in data.items():
        db.set_setting(kalit, qiymat)
    return jsonify({'ok': True})


@app.route('/api/sozlamalar/parol', methods=['POST'])
def sozlamalar_parol():
    data = request.get_json() or {}
    yangi_parol = data.get('yangi_parol', '').strip()
    if not yangi_parol:
        return jsonify({'xato': "Yangi parolni kiriting"}), 400
    db.parol_ornatish(yangi_parol)
    return jsonify({'ok': True})


# ---------------------------------------------------------------------------
# SHABLONLAR (Word) — ko'rish / yuklash / qayta tayyorlash
# ---------------------------------------------------------------------------
def _shablon_maplanishi():
    return {
        'xat': (letters.TEMPLATE_PATH, 'xat_shablon.docx'),
        'sugurta': (letters.SUGURTA_XABARNOMA_TEMPLATE_PATH, 'sugurta_xabarnoma_shablon.docx'),
        'yigma_jild': (letters.YIGMA_JILD_TITUL_TEMPLATE_PATH, 'yigma_jild_titul_shablon.docx'),
    }


@app.route('/api/shablon/davo_turlari', methods=['GET'])
def shablon_davo_turlari():
    return jsonify({'turlari': letters.DAVO_ARIZA_NOMLARI})


@app.route('/api/shablon/korish', methods=['GET'])
def shablon_korish():
    from flask import send_file
    turi = request.args.get('turi')
    davo_turi = request.args.get('davo_turi')
    if turi == 'davo' and davo_turi:
        fayl_nomi = letters.DAVO_ARIZA_TEMPLATES.get(davo_turi)
        if not fayl_nomi:
            return jsonify({'xato': "Noma'lum Davo ariza turi"}), 400
        standart = os.path.join(letters._base_dir(), 'templates', fayl_nomi)
    else:
        maplanish = _shablon_maplanishi()
        if turi not in maplanish:
            return jsonify({'xato': "Noma'lum shablon turi"}), 400
        standart, fayl_nomi = maplanish[turi]
    yol = letters._effektiv_shablon_yoli(standart, fayl_nomi)
    if not os.path.exists(yol):
        return jsonify({'xato': 'Shablon fayli topilmadi'}), 404
    return send_file(yol, as_attachment=True, download_name=fayl_nomi)


@app.route('/api/shablon/yuklash', methods=['POST'])
def shablon_yuklash():
    turi = request.form.get('turi')
    davo_turi = request.form.get('davo_turi')
    f = request.files.get('file')
    if not f or not f.filename:
        return jsonify({'xato': 'Fayl yuborilmadi'}), 400
    if not f.filename.lower().endswith('.docx'):
        return jsonify({'xato': "Faqat .docx fayl qabul qilinadi"}), 400

    if turi == 'davo' and davo_turi:
        fayl_nomi = letters.DAVO_ARIZA_TEMPLATES.get(davo_turi)
        if not fayl_nomi:
            return jsonify({'xato': "Noma'lum Davo ariza turi"}), 400
    else:
        maplanish = _shablon_maplanishi()
        if turi not in maplanish:
            return jsonify({'xato': "Noma'lum shablon turi"}), 400
        _, fayl_nomi = maplanish[turi]

    papka = letters._shablon_papkasi()
    maqsad = os.path.join(papka, fayl_nomi)
    if os.path.exists(maqsad):
        zaxira = maqsad + '.' + datetime.datetime.now().strftime('%Y%m%d%H%M%S') + '.bak'
        import shutil
        shutil.copy2(maqsad, zaxira)
    mustahkam_fayl_saqlash(f, maqsad, ozbek_kengaytma_tekshiruvi=False)
    return jsonify({'ok': True})


@app.route('/api/shablon/qayta_tayyorlash', methods=['POST'])
def shablon_qayta_tayyorlash():
    """Yangi shablon yuklangandan keyin, hali yuborilmagan/olib kelinmagan
    hujjatlarni yangi shablon bilan qayta yaratadi."""
    data = request.get_json() or {}
    turi = data.get('turi')
    davo_turi = data.get('davo_turi')
    settings = db.get_all_settings()

    if turi == 'xat':
        pending = db.get_xatlar('tayyor')
        updated, xatolar = 0, []
        for xat in pending:
            try:
                prow = db.get_portfel_by_id(xat['portfel_id'])
                if not prow:
                    continue
                mijoz_turi_calc, mijoz = util.resolve_mijoz(prow)
                mijoz_ism = mijoz['ism'] if mijoz else prow.get('mijoz_nomi', '')
                mijoz_ism = util.mijoz_ism_hujjat_uchun(mijoz_ism, mijoz_turi_calc)
                mijoz_manzil = mijoz['manzil'] if mijoz else ''
                rahbar_ism = mijoz.get('rahbar_ism') if mijoz else ''
                letters.generate_letter(
                    output_path=xat['fayl_yoli'], xat_turi=xat['xat_turi'], mijoz_ism=mijoz_ism,
                    mijoz_manzil=mijoz_manzil, portfel_row=prow, settings=settings,
                    anketa_raqami=xat['anketa_raqami'], rahbar_ism=rahbar_ism,
                )
                updated += 1
            except Exception as e:
                xatolar.append(f"{xat.get('anketa_raqami')}: {e}")
        return jsonify({'yangilandi': updated, 'xatolar': xatolar})

    elif turi == 'davo' and davo_turi:
        pending = db.get_davo_ariza_pending_by_turi(davo_turi)
        updated, xatolar = 0, []
        for xat in pending:
            try:
                prow = db.get_portfel_by_id(xat['portfel_id'])
                if not prow:
                    continue
                mijoz_turi_calc, mijoz = util.resolve_mijoz(prow)
                taminot = db.get_taminot(xat['anketa_raqami'])
                try:
                    xat_sanasi = datetime.datetime.fromisoformat(xat['yaratilgan_sana']).strftime('%d.%m.%Y')
                except Exception:
                    xat_sanasi = ''
                letters.generate_davo_ariza_v2(
                    davo_turi, xat['davo_ariza_fayl_yoli'], prow, mijoz, taminot, settings,
                    xat_sanasi=xat_sanasi,
                    xat_turi_nomi=('Талабнома' if xat['xat_turi'] == 'Talabnoma' else 'Огохлантириш хати'),
                )
                updated += 1
            except Exception as e:
                xatolar.append(f"{xat.get('anketa_raqami')}: {e}")
        return jsonify({'yangilandi': updated, 'xatolar': xatolar})

    return jsonify({'xato': "Bu shablon turi uchun avtomatik qayta tayyorlash mavjud emas"}), 400


# ---------------------------------------------------------------------------
# BOSH SAHIFA / DASHBOARD
# ---------------------------------------------------------------------------
@app.route('/api/dashboard/summary', methods=['GET'])
def dashboard_summary():
    conn = db.get_conn()
    portfel_soni = conn.execute("SELECT COUNT(*) c FROM portfel WHERE faol=1").fetchone()['c']
    conn.close()

    kun45 = len(db.get_portfel_45_kun(45))
    yuborilmagan = len(db.get_xatlar('tayyor'))
    muddati_otgan = len(db.get_xatlar('muddati_otgan'))

    bugun = __import__('datetime').date.today().isoformat()
    conn = db.get_conn()
    bugun_yaratilgan = conn.execute(
        "SELECT COUNT(*) c FROM xatlar WHERE date(yaratilgan_sana)=?", (bugun,)).fetchone()['c']
    bugun_yuborilgan = conn.execute(
        "SELECT COUNT(*) c FROM xatlar WHERE date(yuborilgan_sana)=?", (bugun,)).fetchone()['c']
    conn.close()

    toliq = db.get_tarmoq_stage3_breakdown_toliq(limit=8)

    def xavfsiz(fn, standart=0):
        try:
            return fn()
        except Exception:
            return standart

    davo_muddati_otgan = xavfsiz(lambda: len(db.get_davo_ariza_muddati_otganlar()))
    sud_muddati_otgan = xavfsiz(lambda: len(db.get_sud_topshirish_muddati_otganlar()))
    mib_harakatsiz = xavfsiz(lambda: len(db.get_mib_harakatsizlar()))
    chora_soni = xavfsiz(lambda: len(db.get_chora_korish_royxati()))
    sugurta_kutilmoqda = xavfsiz(lambda: len(db.get_sugurta_javob_kutilayotganlar()))

    return jsonify({
        'portfeldagi_kreditlar': portfel_soni,
        'kun45_otgan': kun45,
        'yuborilmagan_xatlar': yuborilmagan,
        'muddati_otgan_xatlar': muddati_otgan,
        'bugun_yaratilgan': bugun_yaratilgan,
        'bugun_yuborilgan': bugun_yuborilgan,
        'davo_muddati_otgan': davo_muddati_otgan,
        'sud_muddati_otgan': sud_muddati_otgan,
        'mib_harakatsiz': mib_harakatsiz,
        'chora_soni': chora_soni,
        'sugurta_kutilmoqda': sugurta_kutilmoqda,
        'tarmoq_jadval': toliq,
    })


@app.route('/api/dashboard/qidirish', methods=['GET'])
def dashboard_qidirish():
    anketa = request.args.get('anketa', '').strip()
    natija = db.get_mijoz_holati_anketa_boyicha(anketa)
    if not natija:
        return jsonify({'topildi': False})
    natija_out = []
    for item in natija:
        prow = item['portfel']
        xat = item['xat']
        turi = util.turi_kodidan(prow.get('mijoz_turi_kodi'), prow.get('mijoz_turi'))
        jami = (prow.get('asosiy_qarz') or 0) + (prow.get('foiz_qarz') or 0) + (prow.get('jarima') or 0)
        bosqich = "Hali xat yaratilmagan"
        if xat:
            if xat.get('mib_holati') == 'otkazildi':
                bosqich = "✓ MIBga o'tkazilgan" + (" (yakunlangan)" if xat.get('mib_yakunlangan') else "")
            elif xat.get('sud_holati') == 'topshirildi':
                bosqich = "✓ Sudga topshirilgan"
            elif xat.get('davo_ariza_holati') == 'olib_kelindi':
                bosqich = "✓ Davo ariza (SSPdan olib kelingan)"
            elif xat.get('davo_ariza_fayl_yoli'):
                bosqich = "Davo ariza tayyorlangan"
            else:
                bosqich = {'tayyor': 'Xat tayyor (yuborilmagan)', 'yuborildi': '✓ Xat yuborilgan',
                           'muddati_otgan': "⚠ Xat muddati o'tgan"}.get(xat.get('holat'), xat.get('holat', ''))
        natija_out.append({
            'anketa_raqami': prow['anketa_raqami'], 'mijoz_nomi': prow.get('mijoz_nomi', ''),
            'turi': turi, 'jami_qarz': jami, 'bosqich': bosqich,
            'mib_ish_raqami': xat.get('mib_ish_raqami', '') if xat else '',
            'oxirgi_mib_amal': AMAL_TURLARI_MAP.get(item['oxirgi_mib_amal']['amal_turi'], '') if item.get('oxirgi_mib_amal') else '',
            'xat_fayl': xat.get('fayl_yoli', '') if xat else '',
            'davo_fayl': xat.get('davo_ariza_fayl_yoli', '') if xat else '',
            'yigma_jild_fayl': xat.get('yigma_jild_titul_fayl', '') if xat else '',
        })
    return jsonify({'topildi': True, 'natija': natija_out})


@app.route('/api/dashboard/songgi_harakatlar', methods=['GET'])
def dashboard_songgi_harakatlar():
    xatlar = db.get_xatlar()[:10]
    status_labels = {'tayyor': 'Tayyor', 'yuborildi': "✓ Yuborildi", 'muddati_otgan': "⚠ Muddati o'tgan"}
    natija = []
    for r in xatlar:
        try:
            sana = datetime.datetime.fromisoformat(r['yaratilgan_sana']).strftime('%d.%m.%Y %H:%M')
        except Exception:
            sana = r.get('yaratilgan_sana', '') or ''
        if r.get('mib_holati') == 'otkazildi':
            holat_matni = "✓ MIBga o'tkazilgan" + (" (yakunlangan)" if r.get('mib_yakunlangan') else "")
        elif r.get('sud_holati') == 'topshirildi':
            holat_matni = "✓ Sudga topshirilgan"
        elif r.get('davo_ariza_holati') == 'olib_kelindi':
            holat_matni = "✓ Davo ariza (SSPdan olib kelingan)"
        elif r.get('davo_ariza_fayl_yoli'):
            holat_matni = "Davo ariza tayyorlangan"
        else:
            holat_matni = status_labels.get(r['holat'], r['holat'])
        natija.append({'sana': sana, 'mijoz_nomi': r['mijoz_nomi'], 'xat_turi': r['xat_turi'], 'holat_matni': holat_matni,
                        'xat_fayl': r.get('fayl_yoli', ''), 'davo_fayl': r.get('davo_ariza_fayl_yoli', '')})
    return jsonify({'royxat': natija})


# ---------------------------------------------------------------------------
# TALABNOMA
# ---------------------------------------------------------------------------
@app.route('/api/talabnoma/royxat', methods=['GET'])
def talabnoma_royxat():
    """45+ kun muddati o'tgan, xat yaratilmagan/muddati o'tgan mijozlar ro'yxati."""
    only_new = request.args.get('only_new', 'true') == 'true'
    rows = db.get_portfel_45_kun(45)

    # MUHIM (tezlik): har bir anketa uchun ALOHIDA baza so'rovi (6000+
    # marta) qilish o'rniga — mavjud barcha anketalarni BITTA so'rovda
    # olib, xotirada (Python to'plami) tekshiramiz. Bu ro'yxatni
    # sekundlardan millisekundlargacha tezlashtiradi.
    conn = db.get_conn()
    mavjud_anketalar = {r['anketa_raqami'] for r in
                         conn.execute('SELECT DISTINCT anketa_raqami FROM xatlar').fetchall()}
    conn.close()

    natija = []
    for r in rows:
        turi, mijoz = util.resolve_mijoz(r)
        mavjud = r['anketa_raqami'] in mavjud_anketalar
        if only_new and mavjud:
            continue
        jami = (r.get('asosiy_qarz') or 0) + (r.get('foiz_qarz') or 0) + (r.get('jarima') or 0)
        natija.append({
            'id': r['id'],
            'anketa_raqami': r['anketa_raqami'],
            'mijoz_nomi': r['mijoz_nomi'],
            'turi': turi,
            'dpd': r.get('dpd_max', 0),
            'jami_qarz': jami,
            'mijoz_topildi': bool(mijoz),
            'xat_mavjud': mavjud,
        })
    return jsonify({'royxat': natija, 'jami': len(rows)})


@app.route('/api/talabnoma/xat_yaratish', methods=['POST'])
def talabnoma_xat_yaratish():
    """Belgilangan anketa(lar) uchun xat(lar) yaratadi — 'Tayyor' holatida qoladi."""
    data = request.get_json() or {}
    anketalar = data.get('anketalar', [])
    settings = db.get_all_settings()

    yaratildi, otkazib_yuborildi, xatolar = 0, 0, []
    for anketa in anketalar:
        if db.xat_mavjudmi(anketa):
            otkazib_yuborildi += 1
            continue
        prow_list = db.get_portfel_by_anketa(anketa)
        if not prow_list:
            xatolar.append(f"{anketa}: portfelda topilmadi")
            continue
        prow = prow_list[0]
        try:
            turi, mijoz = util.resolve_mijoz(prow)
            xat_turi = 'Talabnoma' if turi in ('yuridik', 'yatt') else 'Ogohlantirish'
            mijoz_ism = mijoz['ism'] if mijoz else prow.get('mijoz_nomi', '')
            mijoz_manzil = mijoz['manzil'] if mijoz else ''
            rahbar_ism = mijoz.get('rahbar_ism') if mijoz else ''
            mijoz_ism_rasmiy = util.mijoz_ism_hujjat_uchun(mijoz_ism, turi)

            xatlar_dir = hujjatlar_papkasi()
            bugun = __import__('datetime').date.today().strftime('%d.%m.%Y')
            out_dir = os.path.join(xatlar_dir, bugun, 'Xatlar')
            os.makedirs(out_dir, exist_ok=True)
            fname = f"{letters.safe_filename(mijoz_ism)}_{letters.safe_filename(anketa)}_{xat_turi}.docx"
            out_path = os.path.join(out_dir, fname)

            letters.generate_letter(
                output_path=out_path, xat_turi=xat_turi, mijoz_ism=mijoz_ism_rasmiy,
                mijoz_manzil=mijoz_manzil, portfel_row=prow, settings=settings,
                anketa_raqami=anketa, rahbar_ism=rahbar_ism,
            )
            muddat_kun = int(settings.get('eslatma_muddati_kun', 3))
            db.create_xat(
                portfel_id=prow['id'], anketa_raqami=anketa, mijoz_nomi=mijoz_ism, mijoz_turi=turi,
                xat_turi=xat_turi, fayl_yoli=out_path, muddat_kun=muddat_kun,
            )
            yaratildi += 1
        except Exception as e:
            xatolar.append(f"{anketa}: {e}")

    return jsonify({'yaratildi': yaratildi, 'otkazib_yuborildi': otkazib_yuborildi, 'xatolar': xatolar})


@app.route('/api/talabnoma/xatlar_hisoboti', methods=['GET'])
def talabnoma_xatlar_hisoboti():
    """Yuborilgan barcha xatlar tarixi ('so'nggi qilingan ishlar')."""
    xatlar = db.get_xatlar()
    natija = []
    for x in xatlar[:500]:
        natija.append({
            'id': x['id'], 'anketa_raqami': x['anketa_raqami'], 'mijoz_nomi': x['mijoz_nomi'],
            'mijoz_turi': x['mijoz_turi'], 'xat_turi': x['xat_turi'], 'holat': x['holat'],
            'yaratilgan_sana': x.get('yaratilgan_sana', ''), 'yuborilgan_sana': x.get('yuborilgan_sana', ''),
            'fayl_yoli': x.get('fayl_yoli', ''),
        })
    return jsonify({'royxat': natija, 'jami': len(xatlar)})


@app.route('/api/talabnoma/xat_qidirish', methods=['GET'])
def talabnoma_xat_qidirish():
    anketa = request.args.get('anketa', '').strip()
    conn = db.get_conn()
    rows = conn.execute("SELECT * FROM xatlar WHERE anketa_raqami LIKE ?", (f'%{anketa}%',)).fetchall()
    conn.close()
    return jsonify({'ids': [r['id'] for r in rows]})


@app.route('/api/talabnoma/xat_yuborildi_belgilash_ommaviy', methods=['POST'])
def talabnoma_xat_yuborildi_belgilash_ommaviy():
    data = request.get_json() or {}
    ids = data.get('ids', [])
    for xat_id in ids:
        db.mark_xat_yuborildi(xat_id)
    return jsonify({'ok': True, 'yangilandi': len(ids)})


@app.route('/api/talabnoma/xat_ochirish', methods=['POST'])
def talabnoma_xat_ochirish():
    data = request.get_json() or {}
    ids = data.get('ids', [])
    conn = db.get_conn()
    rows = conn.execute(f"SELECT id, holat FROM xatlar WHERE id IN ({','.join('?' * len(ids))})", ids).fetchall() if ids else []
    conn.close()
    yuborilgan = [r['id'] for r in rows if r['holat'] == 'yuborildi']
    ochiriladigan = [r['id'] for r in rows if r['holat'] != 'yuborildi']
    n = db.delete_xatlar(ochiriladigan) if ochiriladigan else 0
    return jsonify({'ochirildi': n, 'otkazib_yuborildi': len(yuborilgan)})


@app.route('/api/talabnoma/barcha_tayyor_ochirish', methods=['POST'])
def talabnoma_barcha_tayyor_ochirish():
    ids = db.get_xatlar_ids_by_holat('tayyor') + db.get_xatlar_ids_by_holat('muddati_otgan')
    if not ids:
        return jsonify({'ochirildi': 0})
    n = db.delete_xatlar(ids)
    return jsonify({'ochirildi': n})


@app.route('/api/talabnoma/dublikatlarni_tozalash', methods=['POST'])
def talabnoma_dublikatlarni_tozalash():
    dup = db.get_duplicate_xat_anketalar()
    if not dup:
        return jsonify({'topildi': 0, 'ochirildi': 0})
    n = db.tozala_duplikat_xatlar()
    return jsonify({'topildi': len(dup), 'ochirildi': n})


@app.route('/api/talabnoma/qidirish', methods=['GET'])
def talabnoma_qidirish():
    anketa = request.args.get('anketa', '').strip()
    if not anketa:
        return jsonify({'royxat': []})
    rows = db.get_portfel_by_anketa(anketa)
    natija = []
    for r in rows:
        turi, mijoz = util.resolve_mijoz(r)
        mavjud = db.xat_mavjudmi(r['anketa_raqami'])
        jami = (r.get('asosiy_qarz') or 0) + (r.get('foiz_qarz') or 0) + (r.get('jarima') or 0)
        natija.append({
            'id': r['id'], 'anketa_raqami': r['anketa_raqami'], 'mijoz_nomi': r['mijoz_nomi'],
            'turi': turi, 'dpd': r.get('dpd_max', 0), 'jami_qarz': jami,
            'mijoz_topildi': bool(mijoz), 'xat_mavjud': mavjud,
        })
    return jsonify({'royxat': natija})


@app.route('/api/talabnoma/excel_eksport', methods=['POST'])
def talabnoma_excel_eksport():
    from flask import send_file
    import io
    data = request.get_json() or {}
    anketalar = data.get('anketalar', [])
    rows = []
    for anketa in anketalar:
        prow_list = db.get_portfel_by_anketa(anketa)
        if not prow_list:
            continue
        prow = prow_list[0]
        turi, mijoz = util.resolve_mijoz(prow)
        xat_turi = 'Talabnoma' if turi in ('yuridik', 'yatt') else 'Ogohlantirish'
        jami = (prow.get('asosiy_qarz') or 0) + (prow.get('foiz_qarz') or 0) + (prow.get('jarima') or 0)
        rows.append({
            'anketa_raqami': anketa, 'mijoz_nomi': prow.get('mijoz_nomi', ''), 'turi': turi,
            'manzil': mijoz.get('manzil', '') if mijoz else '', 'telefon': mijoz.get('telefon', '') if mijoz else '',
            'dpd_max': prow.get('dpd_max', 0), 'jami_qarz': jami,
            'jami_berilgan_summa': prow.get('jami_berilgan_summa', ''), 'xat_turi': xat_turi,
        })
    buf = io.BytesIO()
    import tempfile
    with tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False) as tmp:
        tmp_path = tmp.name
    importer.export_tahlil_excel(rows, tmp_path)
    return send_file(tmp_path, as_attachment=True, download_name='talabnoma_royxati.xlsx')


@app.route('/api/talabnoma/excel_import', methods=['POST'])
def talabnoma_excel_import():
    import tempfile
    f = request.files.get('file')
    if not f:
        return jsonify({'xato': 'Fayl yuborilmadi'}), 400
    with tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False) as tmp:

        tmp_path = tmp.name

    xato_natija = mustahkam_fayl_saqlash(f, tmp_path)

    if xato_natija:

        return jsonify({'xato': xato_natija[0]}), xato_natija[1]
    try:
        result = importer.import_manzil_updates(tmp_path)
        return jsonify(result)
    except Exception as e:
        return jsonify({'xato': str(e)}), 400
    finally:
        os.remove(tmp_path)


# ---------------------------------------------------------------------------
# DAVO ARIZA
# ---------------------------------------------------------------------------
@app.route('/api/davo-ariza/turlari', methods=['GET'])
def davo_ariza_turlari():
    return jsonify({'turlari': letters.DAVO_ARIZA_NOMLARI})


@app.route('/api/davo-ariza/royxat', methods=['GET'])
def davo_ariza_royxat():
    xatlar = db.get_xatlar_yuborilgan_davo_kerak()

    # Tezlik uchun: barcha ta'minot yozuvlarini BITTA so'rovda olamiz
    conn = db.get_conn()
    taminot_rows = conn.execute('SELECT * FROM davo_taminot').fetchall()
    conn.close()
    taminot_map = {r['anketa_raqami']: dict(r) for r in taminot_rows}

    settings = db.get_all_settings()
    muddat_kun = int(settings.get('davo_ariza_muddati_kun', 5))

    natija = []
    for x in xatlar:
        prow = db.get_portfel_by_id(x['portfel_id'])
        if not prow:
            continue
        taminot = taminot_map.get(x['anketa_raqami'])
        tavsiya_kaliti = letters.tavsiya_ariza_turi(x['mijoz_turi'], taminot)
        tavsiya_nomi = letters.DAVO_ARIZA_NOMLARI.get(tavsiya_kaliti, '')

        jami = (prow.get('asosiy_qarz') or 0) + (prow.get('foiz_qarz') or 0) + (prow.get('jarima') or 0)
        has_davo = bool(x.get('davo_ariza_fayl_yoli'))
        olib_kelindi = x.get('davo_ariza_holati') == 'olib_kelindi'

        holat = 'yoq'
        holat_matni = '—'
        if olib_kelindi:
            holat, holat_matni = 'olib_kelindi', '✓ Olib kelindi'
        elif has_davo:
            try:
                davo_dt = datetime.datetime.fromisoformat(x['davo_ariza_sana'])
                qolgan = muddat_kun - (datetime.datetime.now() - davo_dt).days
                if qolgan < 0:
                    holat, holat_matni = 'otgan', f"⚠ {abs(qolgan)} kun o'tib ketdi"
                else:
                    holat, holat_matni = 'tayyor', f"Tayyor — {qolgan} kun qoldi"
            except Exception:
                holat, holat_matni = 'tayyor', 'Tayyor'

        davo_summasi = (x.get('davo_summasi_asosiy') or 0) + (x.get('davo_summasi_foiz') or 0) + \
            (x.get('davo_summasi_jarima') or 0) if has_davo else 0

        summa_farqi_matn = '—'
        qoshimcha_kerak = False
        if has_davo:
            farqi = db.get_davo_ariza_farqi(x, prow, settings=settings)
            if farqi['farq'] > 0:
                summa_farqi_matn = f"+{format(int(farqi['farq']), ',').replace(',', ' ')} so'm"
                qoshimcha_kerak = farqi['qoshimcha_kerak']
                if qoshimcha_kerak:
                    summa_farqi_matn += " ⚠ Qo'shimcha ariza kerak"

        natija.append({
            'xat_id': x['id'],
            'anketa_raqami': x['anketa_raqami'],
            'mijoz_nomi': x['mijoz_nomi'],
            'turi': x['mijoz_turi'],
            'jami_qarz': jami,
            'davo_summasi': davo_summasi,
            'davo_ariza_turi': x.get('davo_ariza_turi'),
            'tavsiya_kaliti': tavsiya_kaliti,
            'tavsiya_nomi': tavsiya_nomi,
            'holat': holat,
            'holat_matni': holat_matni,
            'ish_raqami': x.get('davo_ariza_ish_raqami', ''),
            'taminot_bor': bool(taminot and taminot.get('taminot_turi') not in (None, '', 'yoq')),
            'summa_farqi_matn': summa_farqi_matn,
            'qoshimcha_kerak': qoshimcha_kerak,
        })
    return jsonify({'royxat': natija})


@app.route('/api/davo-ariza/yaratish', methods=['POST'])
def davo_ariza_yaratish():
    """Belgilangan anketalar uchun (har biriga o'z ta'minotiga qarab
    avtomatik to'g'ri tur tanlab) Davo ariza tayyorlaydi."""
    data = request.get_json() or {}
    anketalar = data.get('anketalar', [])
    settings = db.get_all_settings()

    yaratildi, otkazib_yuborildi, xatolar = 0, 0, []
    turlar_soni = {}
    for anketa in anketalar:
        if db.davo_ariza_mavjudmi(anketa):
            otkazib_yuborildi += 1
            continue
        xat = None
        conn = db.get_conn()
        row = conn.execute("SELECT * FROM xatlar WHERE anketa_raqami=? AND holat='yuborildi'",
                            (anketa,)).fetchone()
        conn.close()
        xat = dict(row) if row else None
        if not xat:
            xatolar.append(f"{anketa}: xat topilmadi")
            continue
        prow = db.get_portfel_by_id(xat['portfel_id'])
        if not prow:
            xatolar.append(f"{anketa}: portfel topilmadi")
            continue
        try:
            mijoz_turi_calc, mijoz = util.resolve_mijoz(prow)
            taminot = db.get_taminot(anketa)
            turi = letters.tavsiya_ariza_turi(mijoz_turi_calc, taminot)
            turlar_soni[turi] = turlar_soni.get(turi, 0) + 1

            bugun = __import__('datetime').date.today().strftime('%d.%m.%Y')
            out_dir = os.path.join(hujjatlar_papkasi(), bugun, 'Davo ariza')
            os.makedirs(out_dir, exist_ok=True)
            mijoz_ism = mijoz['ism'] if mijoz else xat['mijoz_nomi']
            fname = f"{letters.safe_filename(mijoz_ism)}_{letters.safe_filename(anketa)}_Davo_{turi}.docx"
            out_path = os.path.join(out_dir, fname)

            xat_sana = ''
            try:
                xat_sana = datetime.datetime.fromisoformat(xat['yaratilgan_sana']).strftime('%d.%m.%Y')
            except Exception:
                pass
            letters.generate_davo_ariza_v2(
                turi, out_path, prow, mijoz, taminot, settings, xat_sanasi=xat_sana,
                xat_turi_nomi=('Талабнома' if xat['xat_turi'] == 'Talabnoma' else 'Огохлантириш хати'),
            )
            db.mark_davo_ariza_yaratildi(xat['id'], out_path, turi=turi, portfel_row=prow)
            yaratildi += 1
        except Exception as e:
            xatolar.append(f"{anketa}: {e}")

    return jsonify({
        'yaratildi': yaratildi, 'otkazib_yuborildi': otkazib_yuborildi,
        'xatolar': xatolar, 'turlar_soni': turlar_soni,
    })


@app.route('/api/davo-ariza/taminot', methods=['GET'])
def davo_ariza_taminot_get():
    anketa = request.args.get('anketa', '').strip()
    t = db.get_taminot(anketa) or {}
    return jsonify(t)


@app.route('/api/davo-ariza/taminot', methods=['POST'])
def davo_ariza_taminot_save():
    data = request.get_json() or {}
    anketa = data.pop('anketa_raqami', None)
    if not anketa:
        return jsonify({'xato': 'anketa_raqami kerak'}), 400
    db.upsert_taminot(anketa, **data)
    return jsonify({'ok': True})


@app.route('/api/davo-ariza/olib_kelindi', methods=['POST'])
def davo_ariza_olib_kelindi():
    anketa = request.form.get('anketa_raqami')
    ish_raqami = request.form.get('ish_raqami', '')
    sana = request.form.get('sana', '')
    f = request.files.get('skan')
    if not anketa or not ish_raqami or not sana:
        return jsonify({'xato': 'anketa_raqami, ish_raqami va sana kerak'}), 400
    if not f or not f.filename:
        return jsonify({'xato': "SSPdan olib kelingan hujjat skanini (PDF) yuklash majburiy"}), 400

    conn = db.get_conn()
    row = conn.execute("SELECT * FROM xatlar WHERE anketa_raqami=? AND davo_ariza_fayl_yoli IS NOT NULL",
                        (anketa,)).fetchone()
    conn.close()
    if not row:
        return jsonify({'xato': 'Davo arizasi tayyorlangan xat topilmadi'}), 404
    xat = dict(row)
    prow = db.get_portfel_by_id(xat['portfel_id'])
    settings = db.get_all_settings()

    # MUHIM: SSPdan olib kelingan (imzo/muhr bilan tasdiqlangan) rasmiy skan
    # Davo ariza faylining o'rniga saqlanadi — bu yig'ma jildga ham
    # avtomatik rasmiy nusxa tushishini ta'minlaydi.
    out_dir = os.path.join(hujjatlar_papkasi(), 'SSP_tasdiqlangan')
    os.makedirs(out_dir, exist_ok=True)
    ext = os.path.splitext(f.filename)[1] or '.pdf'
    dest = os.path.join(out_dir, f"SSP_tasdiqlangan_{letters.safe_filename(xat['mijoz_nomi'])}_{anketa}{ext}")
    xato_natija = mustahkam_fayl_saqlash(f, dest, ozbek_kengaytma_tekshiruvi=False)
    if xato_natija:
        return jsonify({'xato': xato_natija[0]}), xato_natija[1]
    db.update_davo_ariza_fayl(xat['id'], dest)

    qoshimcha_kerak = db.mark_davo_ariza_olib_kelindi(xat['id'], ish_raqami, sana, portfel_row=prow, settings=settings)
    natija = {'ok': True}
    if qoshimcha_kerak:
        xat_yangi = db.get_xat_by_id(xat['id'])
        farqi = db.get_davo_ariza_farqi(xat_yangi, prow, settings)
        natija['ogohlantirish'] = (
            f"Davo ariza 'Olib kelindi' deb belgilandi. Diqqat: joriy qarzdorlik Davo ariza "
            f"yaratilgan paytdagi summadan {int(farqi['farq']):,} so'mga oshib ketgan — "
            f"qo'shimcha (yangi) SSP davo ariza kiritish talab qilinadi.".replace(',', ' ')
        )
    return jsonify(natija)


@app.route('/api/davo-ariza/ochirish', methods=['POST'])
def davo_ariza_ochirish():
    data = request.get_json() or {}
    anketalar = data.get('anketalar', [])
    ochirildi, otkazib_yuborildi = 0, 0
    for anketa in anketalar:
        conn = db.get_conn()
        row = conn.execute("SELECT * FROM xatlar WHERE anketa_raqami=? AND davo_ariza_fayl_yoli IS NOT NULL",
                            (anketa,)).fetchone()
        conn.close()
        if not row:
            otkazib_yuborildi += 1
            continue
        xat = dict(row)
        if xat.get('sud_holati') == 'topshirildi':
            otkazib_yuborildi += 1
            continue
        db.reset_davo_ariza(xat['id'])
        ochirildi += 1
    return jsonify({'ochirildi': ochirildi, 'otkazib_yuborildi': otkazib_yuborildi})


@app.route('/api/davo-ariza/taminot_excel_eksport', methods=['POST'])
def davo_ariza_taminot_excel_eksport():
    import tempfile
    from flask import send_file
    data = request.get_json() or {}
    anketalar = data.get('anketalar', [])
    rows = []
    for anketa in anketalar:
        prow_list = db.get_portfel_by_anketa(anketa)
        mijoz_nomi = prow_list[0].get('mijoz_nomi', '') if prow_list else ''
        t = db.get_taminot(anketa) or {}
        rows.append({'anketa_raqami': anketa, 'mijoz_nomi': mijoz_nomi, **t})
    with tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False) as tmp:
        tmp_path = tmp.name
    importer.export_taminot_excel(rows, tmp_path)
    return send_file(tmp_path, as_attachment=True, download_name='taminot.xlsx')


@app.route('/api/davo-ariza/taminot_excel_import', methods=['POST'])
def davo_ariza_taminot_excel_import():
    import tempfile
    f = request.files.get('file')
    if not f:
        return jsonify({'xato': 'Fayl yuborilmadi'}), 400
    with tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False) as tmp:

        tmp_path = tmp.name

    xato_natija = mustahkam_fayl_saqlash(f, tmp_path)

    if xato_natija:

        return jsonify({'xato': xato_natija[0]}), xato_natija[1]
    try:
        result = importer.import_taminot_excel(tmp_path)
        return jsonify(result)
    except Exception as e:
        return jsonify({'xato': str(e)}), 400
    finally:
        os.remove(tmp_path)


@app.route('/api/davo-ariza/imzodan_excel_eksport', methods=['POST'])
def davo_ariza_imzodan_excel_eksport():
    import tempfile
    from flask import send_file
    import pandas as pd
    royxat = db.get_olib_kelinganlar_royxati()
    if not royxat:
        return jsonify({'xato': "Hali Palatadan/imzodan qaytgan Davo arizalar yo'q."}), 400
    rows = []
    for item in royxat:
        xat = item['xat']
        rows.append({
            'Anketa raqami': xat['anketa_raqami'], 'Mijoz': xat['mijoz_nomi'],
            'PINFL/STIR': item.get('pinfl', ''),
            'Ish raqami': xat.get('davo_ariza_ish_raqami', '') or '',
            'Chiqqan sana': xat.get('davo_ariza_imzo_sana', '') or '',
            'Davo summasi (asosiy)': item['davo_summasi_asosiy'],
            'Davo summasi (foiz)': item['davo_summasi_foiz'],
            'Davo summasi (jarima)': item['davo_summasi_jarima'],
            'Jami davo summasi': item['davo_summasi'],
            'Joriy qarzdorlik': item['joriy_qarz'],
            'Farq': item['farq'],
            "Qo'shimcha ariza kerakmi": "✓ Ha" if item['qoshimcha_kerak'] else "Yo'q",
        })
    df = pd.DataFrame(rows)
    with tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False) as tmp:
        tmp_path = tmp.name
    df.to_excel(tmp_path, index=False)
    return send_file(tmp_path, as_attachment=True, download_name='imzodan_kelganlar.xlsx')


@app.route('/api/davo-ariza/sudga_topshirilganlar_excel', methods=['GET'])
def sudga_topshirilganlar_excel():
    import tempfile
    from flask import send_file
    import pandas as pd
    royxat = db.get_sudga_topshirilganlar_royxati()
    if not royxat:
        return jsonify({'xato': "Hozircha sudga topshirilgan ish yo'q"}), 400
    rows = []
    for item in royxat:
        xat = item['xat']
        prow = item['portfel']
        turi = util.turi_kodidan(prow.get('mijoz_turi_kodi'), prow.get('mijoz_turi')) if prow else xat.get('mijoz_turi')
        asosiy = xat.get('davo_summasi_asosiy') or 0
        foiz = xat.get('davo_summasi_foiz') or 0
        jarima = xat.get('davo_summasi_jarima') or 0
        rows.append({
            'Anketa raqami': xat['anketa_raqami'], 'Mijoz': xat['mijoz_nomi'], 'Turi': turi,
            'Davo summasi (jami)': asosiy + foiz + jarima,
            'Davo ariza tasdiqlangan (imzo) sanasi': xat.get('davo_ariza_imzo_sana', '') or '',
            'Sud ish raqami': xat.get('sud_ish_raqami', '') or '',
            'Sudga topshirilgan sana': xat.get('sud_topshirilgan_sana', '') or '',
            'Asosiy qarz': asosiy, 'Foiz': foiz, 'Penya (jarima)': jarima,
        })
    df = pd.DataFrame(rows)
    with tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False) as tmp:
        tmp_path = tmp.name
    df.to_excel(tmp_path, index=False)
    return send_file(tmp_path, as_attachment=True, download_name='sudga_topshirilganlar.xlsx')


@app.route('/api/davo-ariza/reestr', methods=['POST'])
def davo_ariza_reestr():
    import tempfile
    from flask import send_file
    data = request.get_json() or {}
    anketalar = data.get('anketalar', [])
    xat_raqami = data.get('xat_raqami', '')
    xat_sanasi = data.get('xat_sanasi', '')
    settings = db.get_all_settings()

    mijozlar_royxati = []
    for anketa in anketalar:
        conn = db.get_conn()
        row = conn.execute("SELECT * FROM xatlar WHERE anketa_raqami=? AND davo_ariza_fayl_yoli IS NOT NULL",
                            (anketa,)).fetchone()
        conn.close()
        if not row:
            continue
        xat = dict(row)
        davo_summasi = (xat.get('davo_summasi_asosiy') or 0) + (xat.get('davo_summasi_foiz') or 0) + \
            (xat.get('davo_summasi_jarima') or 0)
        mijozlar_royxati.append({
            'anketa_raqami': anketa, 'mijoz_ism': xat['mijoz_nomi'], 'summa': davo_summasi,
        })
    if not mijozlar_royxati:
        return jsonify({'xato': "Davo arizasi tayyorlangan mijoz topilmadi"}), 400

    with tempfile.NamedTemporaryFile(suffix='.docx', delete=False) as tmp:
        tmp_path = tmp.name
    letters.generate_reestr_ssp(tmp_path, mijozlar_royxati, xat_raqami, xat_sanasi, settings)
    return send_file(tmp_path, as_attachment=True, download_name='Reestr_SSP.docx')


@app.route('/api/davo-ariza/birlashtir', methods=['POST'])
def davo_ariza_birlashtir():
    import tempfile
    from flask import send_file
    data = request.get_json() or {}
    anketalar = data.get('anketalar', [])

    fayllar, turlari = [], []
    for anketa in anketalar:
        conn = db.get_conn()
        row = conn.execute("SELECT * FROM xatlar WHERE anketa_raqami=? AND davo_ariza_fayl_yoli IS NOT NULL",
                            (anketa,)).fetchone()
        conn.close()
        if not row:
            continue
        xat = dict(row)
        if xat.get('davo_ariza_fayl_yoli') and os.path.exists(xat['davo_ariza_fayl_yoli']):
            fayllar.append(xat['davo_ariza_fayl_yoli'])
            turlari.append(xat.get('davo_ariza_turi'))

    if len(fayllar) < 2:
        return jsonify({'xato': "Birlashtirish uchun kamida 2 ta tayyorlangan Davo ariza kerak"}), 400

    bir_xil = len(set(turlari)) <= 1
    ext = '.docx' if bir_xil else '.pdf'
    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        tmp_path = tmp.name
    natija_yoli = letters.birlashtir_hujjatlar(fayllar, tmp_path, ariza_turlari=turlari)
    dl_name = 'Birlashgan_Davo_arizalar' + os.path.splitext(natija_yoli)[1]
    return send_file(natija_yoli, as_attachment=True, download_name=dl_name)


# ---------------------------------------------------------------------------
# SUD ISHLARI
# ---------------------------------------------------------------------------
@app.route('/api/sud/topshirish_kerak', methods=['GET'])
def sud_topshirish_kerak():
    xatlar = db.get_sud_topshirish_kerak()
    natija = []
    for x in xatlar:
        prow = db.get_portfel_by_id(x['portfel_id'])
        turi = util.turi_kodidan(prow.get('mijoz_turi_kodi'), prow.get('mijoz_turi')) if prow else x.get('mijoz_turi')
        sud_nomi = "Iqtisodiy sud" if turi in ('yuridik', 'yatt') else "Fuqarolik sudi"
        asosiy = x.get('davo_summasi_asosiy') or 0
        foiz = x.get('davo_summasi_foiz') or 0
        jarima = x.get('davo_summasi_jarima') or 0
        natija.append({
            'xat_id': x['id'], 'anketa_raqami': x['anketa_raqami'], 'mijoz_nomi': x['mijoz_nomi'],
            'turi': turi, 'sud_nomi': sud_nomi, 'jami': asosiy + foiz + jarima,
            'asosiy': asosiy, 'foiz': foiz, 'jarima': jarima,
            'imzo_sana': x.get('davo_ariza_imzo_sana', ''),
        })
    return jsonify({'royxat': natija})


@app.route('/api/sud/kiritilmadi', methods=['POST'])
def sud_kiritilmadi():
    """Tasdiqlangan Davo arizani sudga topshirmaslik sababini qayd qiladi.
    'Qarz yopilgan' sababi tanlansa, bu tizim tomonidan HAQIQATAN
    tekshiriladi — agar joriy qarzdorlik hali mavjud bo'lsa, bu sabab
    RAD ETILADI (chunki noto'g'ri ma'lumot bo'lishi mumkin)."""
    anketa = request.form.get('anketa_raqami')
    sababi = request.form.get('sababi')
    sana = request.form.get('sana', '')
    xodim_ism = request.form.get('xodim_ism', '')
    izoh = request.form.get('izoh', '')

    if not anketa or not sababi or not sana:
        return jsonify({'xato': 'anketa_raqami, sababi va sana kerak'}), 400
    if sababi not in ('qarz_yopilgan', 'mijoz_arizasi', 'xodim_iltimosi'):
        return jsonify({'xato': "Noma'lum sabab turi"}), 400

    conn = db.get_conn()
    row = conn.execute(
        "SELECT * FROM xatlar WHERE anketa_raqami=? AND davo_ariza_holati='olib_kelindi'", (anketa,)
    ).fetchone()
    conn.close()
    if not row:
        return jsonify({'xato': 'Mos xat topilmadi'}), 404
    xat = dict(row)

    pdf_fayl = None

    if sababi == 'qarz_yopilgan':
        # MUHIM: bu sababni tizim o'zi tekshiradi — agar portfelda hali
        # qarzdorlik ko'rinsa, xodim yoki mijoz noto'g'ri ma'lumot berayotgan
        # bo'lishi mumkin, shuning uchun bu sabab qat'iyan rad etiladi.
        prow = db.get_portfel_by_id(xat['portfel_id'])
        joriy_qarz = 0
        if prow:
            joriy_qarz = (prow.get('asosiy_qarz') or 0) + (prow.get('foiz_qarz') or 0) + (prow.get('jarima') or 0)
        if joriy_qarz > 1:  # 1 so'mgacha yumaloqlash xatosi sifatida e'tiborga olinmaydi
            return jsonify({'xato': (
                f"Bu sabab qabul qilinmadi: tizim ma'lumotlariga ko'ra, mijozning joriy qarzdorligi "
                f"hali {int(joriy_qarz):,} so'm (yopilmagan). Agar bu noto'g'ri bo'lsa, avval Portfelni "
                f"yangilang yoki boshqa sababni tanlang."
            ).replace(',', ' ')}), 400

    elif sababi == 'mijoz_arizasi':
        f = request.files.get('mijoz_arizasi_pdf')
        if not f or not f.filename:
            return jsonify({'xato': "Mijozning yozma arizasi (PDF) yuklash majburiy"}), 400
        out_dir = os.path.join(hujjatlar_papkasi(), 'Sudga_kiritilmadi_arizalar')
        os.makedirs(out_dir, exist_ok=True)
        ext = os.path.splitext(f.filename)[1] or '.pdf'
        pdf_fayl = os.path.join(out_dir, f"mijoz_arizasi_{letters.safe_filename(xat['mijoz_nomi'])}_{anketa}{ext}")
        xato_natija = mustahkam_fayl_saqlash(f, pdf_fayl, ozbek_kengaytma_tekshiruvi=False)
        if xato_natija:
            return jsonify({'xato': xato_natija[0]}), xato_natija[1]

    elif sababi == 'xodim_iltimosi':
        if not xodim_ism.strip():
            return jsonify({'xato': "Bank xodimining F.I.Sh kiritish majburiy"}), 400
        if not izoh.strip():
            return jsonify({'xato': "Qoldirish sababini kiritish majburiy"}), 400

    db.mark_sud_kiritilmadi(xat['id'], sababi, sana, pdf_fayl=pdf_fayl,
                             xodim_ism=xodim_ism or None, izoh=izoh or None)
    return jsonify({'ok': True})


@app.route('/api/sud/kiritilmagan_royxat', methods=['GET'])
def sud_kiritilmagan_royxat():
    xatlar = db.get_sud_kiritilmagan_royxati()
    sabab_nomlari = {'qarz_yopilgan': "Qarz to'liq yopilgan", 'mijoz_arizasi': "Mijozning yozma arizasi asosida",
                      'xodim_iltimosi': "Bank xodimi iltimosiga asosan"}
    natija = []
    for x in xatlar:
        prow = db.get_portfel_by_id(x['portfel_id'])
        turi = util.turi_kodidan(prow.get('mijoz_turi_kodi'), prow.get('mijoz_turi')) if prow else x.get('mijoz_turi')
        natija.append({
            'anketa_raqami': x['anketa_raqami'], 'mijoz_nomi': x['mijoz_nomi'], 'turi': turi,
            'sababi': x.get('sud_kiritilmadi_sababi'),
            'sababi_nomi': sabab_nomlari.get(x.get('sud_kiritilmadi_sababi'), ''),
            'sana': x.get('sud_kiritilmadi_sana', ''),
            'mijoz_arizasi_pdf': x.get('sud_kiritilmadi_pdf', ''),
            'xodim_ism': x.get('sud_kiritilmadi_xodim_ism', ''),
            'izoh': x.get('sud_kiritilmadi_izoh', ''),
        })
    return jsonify({'royxat': natija})


@app.route('/api/sud/topshirildi', methods=['POST'])
def sud_topshirildi():
    anketa = request.form.get('anketa_raqami')
    ish_raqami = request.form.get('ish_raqami', '')
    sana = request.form.get('sana', '')
    if not anketa or not ish_raqami or not sana:
        return jsonify({'xato': 'anketa_raqami, ish_raqami va sana kerak'}), 400
    conn = db.get_conn()
    row = conn.execute("SELECT id, mijoz_nomi FROM xatlar WHERE anketa_raqami=? AND davo_ariza_holati='olib_kelindi'",
                        (anketa,)).fetchone()
    conn.close()
    if not row:
        return jsonify({'xato': 'Mos xat topilmadi'}), 404

    sud_buyrugi_fayl = None
    f = request.files.get('sud_buyrugi')
    if f and f.filename:
        out_dir = os.path.join(hujjatlar_papkasi(), 'Sud_buyruqlari')
        os.makedirs(out_dir, exist_ok=True)
        ext = os.path.splitext(f.filename)[1] or '.pdf'
        sud_buyrugi_fayl = os.path.join(out_dir, f"sud_buyrugi_{letters.safe_filename(row['mijoz_nomi'])}_{anketa}{ext}")
        mustahkam_fayl_saqlash(f, sud_buyrugi_fayl, ozbek_kengaytma_tekshiruvi=False)

    db.mark_sud_topshirildi(row['id'], ish_raqami, sana, buyruq_fayl=sud_buyrugi_fayl)
    return jsonify({'ok': True})


@app.route('/api/sud/topshirish_shablon', methods=['POST'])
def sud_topshirish_shablon():
    import tempfile
    from flask import send_file
    import pandas as pd
    data = request.get_json() or {}
    anketalar = data.get('anketalar', [])
    xatlar = db.get_sud_topshirish_kerak()
    xat_map = {x['anketa_raqami']: x for x in xatlar}
    settings = db.get_all_settings()
    rows = []
    for anketa in anketalar:
        x = xat_map.get(anketa)
        if not x:
            continue
        prow = db.get_portfel_by_id(x['portfel_id'])
        turi = util.turi_kodidan(prow.get('mijoz_turi_kodi'), prow.get('mijoz_turi')) if prow else x.get('mijoz_turi')
        sud_nomi = "Iqtisodiy sud" if turi in ('yuridik', 'yatt') else "Fuqarolik sudi"
        asosiy = x.get('davo_summasi_asosiy') or 0
        foiz = x.get('davo_summasi_foiz') or 0
        jarima = x.get('davo_summasi_jarima') or 0
        farqi = db.get_davo_ariza_farqi(x, prow, settings=settings) if prow else {'farq': 0, 'qoshimcha_kerak': False}
        rows.append({
            'Anketa raqami': anketa, 'Mijoz': x['mijoz_nomi'], 'Sud turi': sud_nomi,
            'Davo summasi (asosiy)': asosiy, 'Davo summasi (foiz)': foiz, 'Davo summasi (jarima)': jarima,
            'Jami qarzdorlik': asosiy + foiz + jarima,
            "Farq (bugungi qarz bilan)": farqi['farq'],
            "Qo'shimcha ariza kerakmi": "✓ Ha" if farqi['qoshimcha_kerak'] else "Yo'q",
            'Sud ish raqami': '', "Sanasi (kun.oy.yil, masalan 20.08.2026)": '',
        })
    df = pd.DataFrame(rows)
    with tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False) as tmp:
        tmp_path = tmp.name
    df.to_excel(tmp_path, index=False)
    return send_file(tmp_path, as_attachment=True, download_name='sudga_topshirish_shabloni.xlsx')


@app.route('/api/sud/topshirish_excel_import', methods=['POST'])
def sud_topshirish_excel_import():
    import tempfile
    import pandas as pd
    f = request.files.get('file')
    if not f:
        return jsonify({'xato': 'Fayl yuborilmadi'}), 400
    with tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False) as tmp:

        tmp_path = tmp.name

    xato_natija = mustahkam_fayl_saqlash(f, tmp_path)

    if xato_natija:

        return jsonify({'xato': xato_natija[0]}), xato_natija[1]
    try:
        df = pd.read_excel(tmp_path)
    except Exception as e:
        return jsonify({'xato': str(e)}), 400
    finally:
        os.remove(tmp_path)

    sana_col = None
    for c in df.columns:
        if str(c).startswith('Sanasi'):
            sana_col = c
            break
    if 'Anketa raqami' not in df.columns or 'Sud ish raqami' not in df.columns or not sana_col:
        return jsonify({'xato': "Excel ustunlari mos emas — asl shablonni o'zgartirmang"}), 400

    yangilandi, otkazib_yuborildi, topilmadi = 0, 0, 0
    for _, r in df.iterrows():
        anketa = r.get('Anketa raqami')
        if pd.isna(anketa):
            continue
        anketa = str(anketa).strip()
        ish_raqami = r.get('Sud ish raqami')
        sana = r.get(sana_col)
        if pd.isna(ish_raqami) or pd.isna(sana) or not str(ish_raqami).strip() or not str(sana).strip():
            otkazib_yuborildi += 1
            continue
        conn = db.get_conn()
        row = conn.execute(
            "SELECT id FROM xatlar WHERE anketa_raqami=? AND davo_ariza_holati='olib_kelindi' "
            "AND (sud_holati IS NULL OR sud_holati != 'topshirildi')", (anketa,)).fetchone()
        conn.close()
        if not row:
            topilmadi += 1
            continue
        sana_str = str(sana).strip()
        if hasattr(sana, 'strftime'):
            sana_str = sana.strftime('%d.%m.%Y')
        db.mark_sud_topshirildi(row['id'], str(ish_raqami).strip(), sana_str)
        yangilandi += 1
    return jsonify({'yangilandi': yangilandi, 'otkazib_yuborildi': otkazib_yuborildi, 'topilmadi': topilmadi})


@app.route('/api/sud/royxat', methods=['GET'])
def sud_royxat():
    royxat = db.get_sudga_topshirilganlar_royxati()
    natija = []
    for item in royxat:
        xat = item['xat']
        prow = item['portfel']
        turi = util.turi_kodidan(prow.get('mijoz_turi_kodi'), prow.get('mijoz_turi')) if prow else xat.get('mijoz_turi')
        asosiy = xat.get('davo_summasi_asosiy') or 0
        foiz = xat.get('davo_summasi_foiz') or 0
        jarima = xat.get('davo_summasi_jarima') or 0
        natija.append({
            'anketa_raqami': xat['anketa_raqami'], 'mijoz_nomi': xat['mijoz_nomi'], 'turi': turi,
            'jami_sud_summasi': asosiy + foiz + jarima, 'asosiy': asosiy, 'foiz': foiz, 'jarima': jarima,
            'sud_ish_raqami': xat.get('sud_ish_raqami', ''), 'sudga_topshirilgan': xat.get('sud_topshirilgan_sana', ''),
            'mib_holati': "✓ O'tkazilgan" if xat.get('mib_holati') == 'otkazildi' else 'Kutilmoqda',
        })
    return jsonify({'royxat': natija})


@app.route('/api/sud/qidirish', methods=['GET'])
def sud_qidirish():
    anketa = request.args.get('anketa', '').strip()
    xatlar = db.get_sud_topshirish_kerak()
    for x in xatlar:
        if x['anketa_raqami'] == anketa:
            prow = db.get_portfel_by_id(x['portfel_id'])
            turi = util.turi_kodidan(prow.get('mijoz_turi_kodi'), prow.get('mijoz_turi')) if prow else x.get('mijoz_turi')
            sud_nomi = "Iqtisodiy sud" if turi in ('yuridik', 'yatt') else "Fuqarolik sudi"
            asosiy = x.get('davo_summasi_asosiy') or 0
            foiz = x.get('davo_summasi_foiz') or 0
            jarima = x.get('davo_summasi_jarima') or 0
            return jsonify({'topildi': True, 'row': {
                'xat_id': x['id'], 'anketa_raqami': x['anketa_raqami'], 'mijoz_nomi': x['mijoz_nomi'],
                'turi': turi, 'sud_nomi': sud_nomi, 'jami': asosiy + foiz + jarima,
                'imzo_sana': x.get('davo_ariza_imzo_sana', ''),
            }})
    return jsonify({'topildi': False})


@app.route('/api/sud/eski_ish_qidirish', methods=['GET'])
def sud_eski_ish_qidirish():
    anketa = request.args.get('anketa', '').strip()
    rows = db.get_portfel_by_anketa(anketa)
    if not rows:
        return jsonify({'topildi': False})
    prow = rows[0]
    turi, mijoz = util.resolve_mijoz(prow)
    jami = (prow.get('asosiy_qarz') or 0) + (prow.get('foiz_qarz') or 0) + (prow.get('jarima') or 0)
    conn = db.get_conn()
    mavjud = conn.execute("SELECT id FROM xatlar WHERE portfel_id=?", (prow['id'],)).fetchone()
    conn.close()
    return jsonify({'topildi': True, 'mijoz_nomi': prow.get('mijoz_nomi', ''), 'turi': turi,
                     'jami_qarz': jami, 'mavjud_yozuv_bor': bool(mavjud)})


@app.route('/api/sud/eski_ish_kiritish', methods=['POST'])
def sud_eski_ish_kiritish():
    data = request.get_json() or {}
    anketa = data.get('anketa_raqami', '').strip()
    ish_raqami = data.get('ish_raqami', '').strip()
    sana = data.get('sana', '').strip()
    if not anketa or not ish_raqami or not sana:
        return jsonify({'xato': 'anketa_raqami, ish_raqami va sana kerak'}), 400
    rows = db.get_portfel_by_anketa(anketa)
    if not rows:
        return jsonify({'xato': 'Bu anketa portfelda topilmadi'}), 404
    prow = rows[0]
    mijoz_turi = util.turi_kodidan(prow.get('mijoz_turi_kodi'), prow.get('mijoz_turi'))
    db.create_legacy_sud_xat(
        portfel_id=prow['id'], anketa_raqami=anketa, mijoz_nomi=prow.get('mijoz_nomi', ''),
        mijoz_turi=mijoz_turi, sud_ish_raqami=ish_raqami, sud_sana=sana,
    )
    return jsonify({'ok': True})
# ---------------------------------------------------------------------------
@app.route('/api/mib/otkazish_kerak', methods=['GET'])
def mib_otkazish_kerak():
    xatlar = db.get_mib_otkazish_kerak()
    natija = []
    for x in xatlar:
        prow = db.get_portfel_by_id(x['portfel_id'])
        turi = util.turi_kodidan(prow.get('mijoz_turi_kodi'), prow.get('mijoz_turi')) if prow else x.get('mijoz_turi')
        asosiy = x.get('davo_summasi_asosiy') or 0
        foiz = x.get('davo_summasi_foiz') or 0
        jarima = x.get('davo_summasi_jarima') or 0
        natija.append({
            'xat_id': x['id'], 'anketa_raqami': x['anketa_raqami'], 'mijoz_nomi': x['mijoz_nomi'], 'turi': turi,
            'jami': asosiy + foiz + jarima, 'sud_ish_raqami': x.get('sud_ish_raqami', ''),
            'sudga_topshirilgan': x.get('sud_topshirilgan_sana', ''),
        })
    return jsonify({'royxat': natija})


@app.route('/api/mib/otkazish_kerak_excel', methods=['GET'])
def mib_otkazish_kerak_excel():
    import tempfile
    from flask import send_file
    import pandas as pd
    xatlar = db.get_mib_otkazish_kerak()
    if not xatlar:
        return jsonify({'xato': "Hozircha MIBga o'tkazish kerak bo'lgan ish yo'q"}), 400
    rows = []
    for x in xatlar:
        prow = db.get_portfel_by_id(x['portfel_id'])
        turi = util.turi_kodidan(prow.get('mijoz_turi_kodi'), prow.get('mijoz_turi')) if prow else x.get('mijoz_turi')
        asosiy = x.get('davo_summasi_asosiy') or 0
        foiz = x.get('davo_summasi_foiz') or 0
        jarima = x.get('davo_summasi_jarima') or 0
        rows.append({
            'Anketa raqami': x['anketa_raqami'], 'Mijoz': x['mijoz_nomi'], 'Turi': turi,
            "Qarzdorlik (so'm)": asosiy + foiz + jarima,
            'Sud ish raqami': x.get('sud_ish_raqami', '') or '',
            'Sudga topshirilgan sana': x.get('sud_topshirilgan_sana', '') or '',
        })
    df = pd.DataFrame(rows)
    with tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False) as tmp:
        tmp_path = tmp.name
    df.to_excel(tmp_path, index=False)
    return send_file(tmp_path, as_attachment=True, download_name='mibga_otkazish_kerak.xlsx')


@app.route('/api/mib/otkazildi', methods=['POST'])
def mib_otkazildi():
    anketa = request.form.get('anketa_raqami')
    ish_raqami = request.form.get('ish_raqami', '')
    sana = request.form.get('sana', '')
    if not anketa or not ish_raqami or not sana:
        return jsonify({'xato': 'anketa_raqami, ish_raqami va sana kerak'}), 400

    ijro_f = request.files.get('ijro_varaqasi')
    sud_f = request.files.get('sud_buyrugi')
    if not ijro_f or not ijro_f.filename:
        return jsonify({'xato': "Ijro varaqasi (PDF) yuklash majburiy"}), 400
    if not sud_f or not sud_f.filename:
        return jsonify({'xato': "Sud buyrug'i / hal qiluv qarori (PDF) yuklash majburiy"}), 400

    conn = db.get_conn()
    row = conn.execute("SELECT * FROM xatlar WHERE anketa_raqami=? AND sud_holati='topshirildi'", (anketa,)).fetchone()
    conn.close()
    if not row:
        return jsonify({'xato': 'Mos xat topilmadi'}), 404
    xat = dict(row)
    prow = db.get_portfel_by_id(xat['portfel_id'])
    jami = (prow.get('asosiy_qarz') or 0) + (prow.get('foiz_qarz') or 0) + (prow.get('jarima') or 0) if prow else \
        (xat.get('davo_summasi_asosiy') or 0) + (xat.get('davo_summasi_foiz') or 0) + (xat.get('davo_summasi_jarima') or 0)

    out_dir = os.path.join(mib_hujjatlar_papkasi(), letters.safe_filename(anketa))
    os.makedirs(out_dir, exist_ok=True)
    ijro_fayl_yoli = os.path.join(out_dir, 'ijro_varaqasi_' + ijro_f.filename)
    mustahkam_fayl_saqlash(ijro_f, ijro_fayl_yoli, ozbek_kengaytma_tekshiruvi=False)
    sud_fayl_yoli = os.path.join(out_dir, 'sud_buyrugi_' + sud_f.filename)
    mustahkam_fayl_saqlash(sud_f, sud_fayl_yoli, ozbek_kengaytma_tekshiruvi=False)

    db.mark_mib_otkazildi(xat['id'], ish_raqami, sana, ijro_fayl_yoli, mib_ijro_summasi=jami,
                           sud_buyrugi_fayl=sud_fayl_yoli)

    # Yig'ma jild (ish dossiyesi) papkasini ochib, titul (muqova) hujjatini
    # yaratamiz, so'ng shu ishga tegishli AVVAL yaratilgan barcha hujjatlarni
    # (xat, Davo ariza, Sud buyrug'i, Ijro varaqasi) avtomatik jildga
    # nusxalaymiz — foydalanuvchi qo'lda hech narsa qo'shmasa ham, jild
    # to'liq bo'ladi.
    try:
        mijoz_turi_calc, mijoz = util.resolve_mijoz(prow) if prow else (None, None)
        settings = db.get_all_settings()
        xat_yangilangan = db.get_xat_by_id(xat['id'])

        papka_nomi = f"{letters.safe_filename(xat['mijoz_nomi'])}_{letters.safe_filename(anketa)}_{letters.safe_filename(ish_raqami)}"
        jild_papka = os.path.join(hujjatlar_papkasi(), 'Yigma_jildlar', papka_nomi)
        os.makedirs(jild_papka, exist_ok=True)
        titul_path = os.path.join(jild_papka, '00_Titul.docx')
        letters.generate_yigma_jild_titul(titul_path, xat_yangilangan, prow, mijoz, settings)
        db.mark_yigma_jild_yaratildi(xat['id'], jild_papka, titul_path)

        xat_toliq = db.get_xat_by_id(xat['id'])
        db.yigma_jild_toldirish(jild_papka, xat_toliq)
    except Exception as e:
        return jsonify({'ok': True, 'ogohlantirish': f"MIBga o'tkazildi, lekin yig'ma jild yaratishda xato: {e}"})

    return jsonify({'ok': True})


@app.route('/api/mib/faol', methods=['GET'])
def mib_faol():
    xatlar = db.get_mib_faol_royxat()
    settings = db.get_all_settings()
    natija = []
    for x in xatlar:
        prow = db.get_portfel_by_id(x['portfel_id'])
        turi = util.turi_kodidan(prow.get('mijoz_turi_kodi'), prow.get('mijoz_turi')) if prow else x.get('mijoz_turi')
        jami = (prow.get('asosiy_qarz') or 0) + (prow.get('foiz_qarz') or 0) + (prow.get('jarima') or 0) if prow else 0
        amallar = db.get_mib_amallar(x['id'])
        songgi = amallar[-1] if amallar else None
        mib_summasi = x.get('mib_ijro_summasi') or jami
        farq = jami - mib_summasi
        nazorat_natija = None
        if prow:
            try:
                nazorat_natija = db.get_mib_monitoring_holati(x['id'], prow, xat=x, settings=settings)
            except Exception:
                nazorat_natija = None
        nazorat = nazorat_natija.get('holat') if nazorat_natija else None
        nazorat_matn = nazorat_natija.get('xabar', '—') if nazorat_natija and nazorat_natija.get('holat') else '—'
        natija.append({
            'xat_id': x['id'], 'anketa_raqami': x['anketa_raqami'], 'mijoz_nomi': x['mijoz_nomi'], 'turi': turi,
            'qarzdorlik': jami, 'mib_ish_raqami': x.get('mib_ish_raqami', ''),
            'songgi_harakat': AMAL_TURLARI_MAP.get(songgi['amal_turi'], songgi['amal_turi']) if songgi else '—',
            'songgi_harakat_sana': songgi['amal_sanasi'] if songgi else '',
            'harakatlar_soni': len(amallar), 'farq': farq, 'nazorat': nazorat, 'nazorat_matn': nazorat_matn,
            'yigma_jild_titul_fayl': x.get('yigma_jild_titul_fayl', ''),
            'yigma_jild_papka': x.get('yigma_jild_papka', ''),
            'yigma_jild_bor': x.get('yigma_jild_holati') == 'mavjud',
        })
    return jsonify({'royxat': natija})


@app.route('/api/mib/amal_qoshish', methods=['POST'])
def mib_amal_qoshish():
    data = request.get_json() or {}
    anketa = data.get('anketa_raqami')
    conn = db.get_conn()
    row = conn.execute("SELECT id FROM xatlar WHERE anketa_raqami=? AND mib_holati='otkazildi'", (anketa,)).fetchone()
    conn.close()
    if not row:
        return jsonify({'xato': 'Mos MIB ishi topilmadi'}), 404
    db.add_mib_amal(row['id'], data.get('amal_turi', ''), data.get('amal_sanasi', ''), data.get('tavsif', ''),
                     undirilgan_summa=data.get('undirilgan_summa'))
    return jsonify({'ok': True})


@app.route('/api/mib/amallar_tarixi', methods=['GET'])
def mib_amallar_tarixi():
    anketa = request.args.get('anketa', '').strip()
    conn = db.get_conn()
    row = conn.execute("SELECT id FROM xatlar WHERE anketa_raqami=? AND mib_holati='otkazildi'", (anketa,)).fetchone()
    conn.close()
    if not row:
        return jsonify({'amallar': []})
    amallar = db.get_mib_amallar(row['id'])
    return jsonify({'amallar': amallar})


@app.route('/api/mib/qidirish', methods=['GET'])
def mib_qidirish():
    anketa = request.args.get('anketa', '').strip()
    conn = db.get_conn()
    row = conn.execute("SELECT * FROM xatlar WHERE anketa_raqami=? AND mib_holati='otkazildi'", (anketa,)).fetchone()
    conn.close()
    if not row:
        return jsonify({'topildi': False})
    x = dict(row)
    prow = db.get_portfel_by_id(x['portfel_id'])
    turi = util.turi_kodidan(prow.get('mijoz_turi_kodi'), prow.get('mijoz_turi')) if prow else x.get('mijoz_turi')
    jami = (prow.get('asosiy_qarz') or 0) + (prow.get('foiz_qarz') or 0) + (prow.get('jarima') or 0) if prow else 0
    amallar = db.get_mib_amallar(x['id'])
    songgi = amallar[-1] if amallar else None
    return jsonify({'topildi': True, 'row': {
        'xat_id': x['id'], 'anketa_raqami': x['anketa_raqami'], 'mijoz_nomi': x['mijoz_nomi'], 'turi': turi,
        'qarzdorlik': jami, 'mib_ish_raqami': x.get('mib_ish_raqami', ''),
        'songgi_harakat': songgi['amal_turi'] if songgi else '—',
        'songgi_harakat_sana': songgi['amal_sanasi'] if songgi else '',
        'harakatlar_soni': len(amallar),
        'yigma_jild_titul_fayl': x.get('yigma_jild_titul_fayl', ''),
        'yigma_jild_papka': x.get('yigma_jild_papka', ''),
        'yigma_jild_bor': x.get('yigma_jild_holati') == 'mavjud',
    }})


@app.route('/api/mib/jild_fayllari', methods=['GET'])
def mib_jild_fayllari():
    anketa = request.args.get('anketa', '').strip()
    conn = db.get_conn()
    row = conn.execute("SELECT yigma_jild_papka FROM xatlar WHERE anketa_raqami=?", (anketa,)).fetchone()
    conn.close()
    papka = row['yigma_jild_papka'] if row else None
    if not papka or not os.path.isdir(papka):
        return jsonify({'fayllar': [], 'papka': papka or ''})
    fayllar = []
    for f in sorted(os.listdir(papka)):
        toliq = os.path.join(papka, f)
        if os.path.isfile(toliq):
            fayllar.append({'nomi': f, 'yoli': toliq})
    return jsonify({'fayllar': fayllar, 'papka': papka})


@app.route('/api/mib/jarayondagilar_excel', methods=['GET'])
def mib_jarayondagilar_excel():
    import tempfile
    from flask import send_file
    import pandas as pd
    royxat = db.get_mib_faol_royxat_toliq()
    if not royxat:
        return jsonify({'xato': "MIBda jarayondagi hujjatlar yo'q"}), 400
    rows = []
    for item in royxat:
        xat = item['xat']
        amal = item['amal']
        amal_turi_nomi = AMAL_TURLARI_MAP.get(amal['amal_turi'], amal['amal_turi']) if amal else ''
        oylik = amal.get('undirilgan_summa', '') if amal and amal['amal_turi'] == 'oylik_ish_haqqi' else ''
        rows.append({
            'Anketa raqami': xat['anketa_raqami'], 'PINFL/STIR': item.get('pinfl') or item.get('stir', ''),
            "F.I.Sh / Nomi": xat['mijoz_nomi'], 'Turi': xat['mijoz_turi'],
            "Qarzdorlik (so'm)": item['jami_qarz'], 'MIB ish raqami': xat.get('mib_ish_raqami', ''),
            "MIBga o'tkazilgan sana": xat.get('mib_otkazilgan_sana', ''), 'Harakat turi': amal_turi_nomi,
            'Harakat sanasi': amal.get('amal_sanasi', '') if amal else '', 'Tavsif': amal.get('tavsif', '') if amal else '',
            "Oylik ish haqqidan undirilgan summa": oylik,
            "Undirilgan summa (umumiy)": amal.get('undirilgan_summa', '') if amal else '',
        })
    df = pd.DataFrame(rows)
    with tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False) as tmp:
        tmp_path = tmp.name
    df.to_excel(tmp_path, index=False)
    return send_file(tmp_path, as_attachment=True, download_name='mib_jarayondagi_hujjatlar.xlsx')


@app.route('/api/mib/eski_ish_qidirish', methods=['GET'])
def mib_eski_ish_qidirish():
    anketa = request.args.get('anketa', '').strip()
    rows = db.get_portfel_by_anketa(anketa)
    if not rows:
        return jsonify({'topildi': False})
    prow = rows[0]
    turi, mijoz = util.resolve_mijoz(prow)
    jami = (prow.get('asosiy_qarz') or 0) + (prow.get('foiz_qarz') or 0) + (prow.get('jarima') or 0)
    return jsonify({'topildi': True, 'mijoz_nomi': prow.get('mijoz_nomi', ''), 'turi': turi, 'jami_qarz': jami})


@app.route('/api/mib/eski_ish_kiritish', methods=['POST'])
def mib_eski_ish_kiritish():
    data = request.get_json() or {}
    anketa = data.get('anketa_raqami', '').strip()
    ish_raqami = data.get('ish_raqami', '').strip()
    sana = data.get('sana', '').strip()
    if not anketa or not ish_raqami or not sana:
        return jsonify({'xato': 'anketa_raqami, ish_raqami va sana kerak'}), 400
    rows = db.get_portfel_by_anketa(anketa)
    if not rows:
        return jsonify({'xato': 'Bu anketa portfelda topilmadi'}), 404
    prow = rows[0]
    turi, mijoz = util.resolve_mijoz(prow)
    jami = float(data.get('qarzdorlik') or 0) or (
        (prow.get('asosiy_qarz') or 0) + (prow.get('foiz_qarz') or 0) + (prow.get('jarima') or 0))

    conn = db.get_conn()
    mavjud = conn.execute("SELECT id FROM xatlar WHERE anketa_raqami=?", (anketa,)).fetchone()
    if mavjud:
        xat_id = mavjud['id']
        conn.execute("UPDATE xatlar SET mib_holati='otkazildi', mib_ish_raqami=?, mib_otkazilgan_sana=?, "
                     "sud_ish_raqami=COALESCE(sud_ish_raqami, ?), sud_holati='topshirildi', "
                     "mib_ijro_summasi=? WHERE id=?",
                     (ish_raqami, sana, data.get('sud_ish_raqami', ''), jami, xat_id))
    else:
        conn.execute(
            "INSERT INTO xatlar (portfel_id, anketa_raqami, mijoz_nomi, mijoz_turi, holat, "
            "sud_holati, sud_ish_raqami, mib_holati, mib_ish_raqami, mib_otkazilgan_sana, mib_ijro_summasi) "
            "VALUES (?, ?, ?, ?, 'yuborildi', 'topshirildi', ?, 'otkazildi', ?, ?, ?)",
            (prow['id'], anketa, prow.get('mijoz_nomi', ''), turi, data.get('sud_ish_raqami', ''),
             ish_raqami, sana, jami))
        xat_id = conn.execute("SELECT id FROM xatlar WHERE anketa_raqami=?", (anketa,)).fetchone()['id']
    conn.commit()
    conn.close()
    db.add_mib_amal(xat_id, 'eski_ish_kiritildi', sana, "Eski ish sifatida bazaga kiritildi")
    return jsonify({'ok': True})


@app.route('/api/mib/avtomashinalar', methods=['GET'])
def mib_avtomashinalar():
    anketa = request.args.get('anketa', '').strip()
    conn = db.get_conn()
    row = conn.execute("SELECT id FROM xatlar WHERE anketa_raqami=?", (anketa,)).fetchone()
    conn.close()
    if not row:
        return jsonify({'mashinalar': []})
    return jsonify({'mashinalar': db.get_avtomashinalar(row['id'])})


@app.route('/api/mib/avtomashina_qoshish', methods=['POST'])
def mib_avtomashina_qoshish():
    data = request.get_json() or {}
    anketa = data.get('anketa_raqami', '').strip()
    conn = db.get_conn()
    row = conn.execute("SELECT id FROM xatlar WHERE anketa_raqami=?", (anketa,)).fetchone()
    conn.close()
    if not row:
        return jsonify({'xato': 'Mos MIB ishi topilmadi'}), 404
    db.add_avtomashina(row['id'], data.get('rusumi', ''), data.get('davlat_raqami', ''), data.get('pinfl', ''))
    return jsonify({'ok': True})


@app.route('/api/mib/avtomashina_holati', methods=['POST'])
def mib_avtomashina_holati():
    mashina_id = request.form.get('id')
    holati = request.form.get('holati')
    modda = request.form.get('modda', '')
    if not mashina_id or not holati:
        return jsonify({'xato': 'id va holati kerak'}), 400
    fayl_yoli = None
    f = request.files.get('hujjat')
    if f and f.filename:
        out_dir = os.path.join(hujjatlar_papkasi(), 'avtomashina_hujjatlar')
        os.makedirs(out_dir, exist_ok=True)
        fayl_yoli = os.path.join(out_dir, f"{mashina_id}_{f.filename}")
        mustahkam_fayl_saqlash(f, fayl_yoli, ozbek_kengaytma_tekshiruvi=False)
    db.update_avtomashina_holati(mashina_id, holati, asoslovchi_hujjat_fayl=fayl_yoli, modda=modda or None)
    return jsonify({'ok': True})


@app.route('/api/mib/avtomashinalar_import', methods=['POST'])
def mib_avtomashinalar_import():
    import tempfile
    f = request.files.get('file')
    if not f:
        return jsonify({'xato': 'Fayl yuborilmadi'}), 400
    with tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False) as tmp:

        tmp_path = tmp.name

    xato_natija = mustahkam_fayl_saqlash(f, tmp_path)

    if xato_natija:

        return jsonify({'xato': xato_natija[0]}), xato_natija[1]
    try:
        natija = importer.import_avtomashinalar_excel(tmp_path)
        return jsonify(natija)
    except Exception as e:
        return jsonify({'xato': str(e)}), 400
    finally:
        os.remove(tmp_path)


@app.route('/api/mib/avtomashinalar_xatlanmagan_excel', methods=['GET'])
def mib_avtomashinalar_xatlanmagan_excel():
    import tempfile
    from flask import send_file
    import pandas as pd
    xatlanmagan = db.get_xatlanmagan_avtomashinalar()
    if not xatlanmagan:
        return jsonify({'xato': "Xatlanmagan avtomashinalar yo'q"}), 400
    rows = [{'Mashina rusumi': m['mashina_rusumi'], 'Davlat raqami': m['davlat_raqami'],
             'Mijoz PINFL': m.get('mijoz_pinfl', ''), 'Anketa raqami': m['anketa_raqami'],
             'Mijoz nomi': m['mijoz_nomi']} for m in xatlanmagan]
    df = pd.DataFrame(rows)
    with tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False) as tmp:
        tmp_path = tmp.name
    df.to_excel(tmp_path, index=False)
    return send_file(tmp_path, as_attachment=True, download_name='xatlanmagan_avtomashinalar.xlsx')


@app.route('/api/mib/yakunlash', methods=['POST'])
def mib_yakunlash():
    anketa = request.form.get('anketa_raqami')
    sabab = request.form.get('sabab', '')
    sana = request.form.get('sana', '')
    if not anketa or not sabab:
        return jsonify({'xato': 'anketa_raqami va sabab kerak'}), 400
    f = request.files.get('asos_hujjat')
    if not f or not f.filename:
        return jsonify({'xato': "Yakunlash asosi hujjatini (PDF) yuklang — bu majburiy."}), 400
    conn = db.get_conn()
    row = conn.execute("SELECT id FROM xatlar WHERE anketa_raqami=? AND mib_holati='otkazildi'", (anketa,)).fetchone()
    conn.close()
    if not row:
        return jsonify({'xato': 'Mos MIB ishi topilmadi'}), 404
    out_dir = os.path.join(mib_hujjatlar_papkasi(), letters.safe_filename(anketa))
    os.makedirs(out_dir, exist_ok=True)
    fayl_yoli = os.path.join(out_dir, 'yakunlash_asosi_' + f.filename)
    mustahkam_fayl_saqlash(f, fayl_yoli, ozbek_kengaytma_tekshiruvi=False)
    db.mark_mib_yakunlandi(row['id'], sabab, sana or None)
    db.set_mib_yakunlash_hujjati(row['id'], fayl_yoli)
    return jsonify({'ok': True})


@app.route('/api/mib/yakunlangan', methods=['GET'])
def mib_yakunlangan():
    xatlar = db.get_mib_yakunlangan_royxati()
    natija = []
    for x in xatlar:
        prow = db.get_portfel_by_id(x['portfel_id'])
        turi = util.turi_kodidan(prow.get('mijoz_turi_kodi'), prow.get('mijoz_turi')) if prow else x.get('mijoz_turi')
        natija.append({
            'xat_id': x['id'], 'anketa_raqami': x['anketa_raqami'], 'mijoz_nomi': x['mijoz_nomi'], 'turi': turi,
            'mib_ish_raqami': x.get('mib_ish_raqami', ''), 'yakunlangan_sana': x.get('mib_yakunlangan_sana', ''),
            'sabab': x.get('mib_yakunlash_sababi', ''),
        })
    return jsonify({'royxat': natija})


@app.route('/api/mib/qayta_ochish', methods=['POST'])
def mib_qayta_ochish():
    data = request.get_json() or {}
    anketa = data.get('anketa_raqami')
    conn = db.get_conn()
    row = conn.execute("SELECT id FROM xatlar WHERE anketa_raqami=? AND mib_yakunlangan=1", (anketa,)).fetchone()
    conn.close()
    if not row:
        return jsonify({'xato': 'Topilmadi'}), 404
    db.mib_ishni_qayta_ochish(row['id'])
    return jsonify({'ok': True})


# ---------------------------------------------------------------------------
# CHORA KO'RISH
# ---------------------------------------------------------------------------
@app.route('/api/chora/royxat', methods=['GET'])
def chora_royxat():
    royxat = db.get_chora_korish_royxati()
    natija = []
    for i, r in enumerate(royxat):
        prow = r['portfel']
        jami = (prow.get('asosiy_qarz') or 0) + (prow.get('foiz_qarz') or 0) + (prow.get('jarima') or 0)
        natija.append({
            'i': i, 'anketa_raqami': prow['anketa_raqami'], 'mijoz_nomi': prow['mijoz_nomi'],
            'turi': r['turi'], 'dpd': r['dpd'], 'qarzdorlik': jami,
            'chora': r['chora'], 'chora_nomi': r['chora_nomi'], 'tafsilot': r['tafsilot'],
        })
    soni = {}
    for r in royxat:
        soni[r['chora']] = soni.get(r['chora'], 0) + 1
    return jsonify({'royxat': natija, 'soni': soni, 'chora_nomlari': db.CHORA_NOMLARI})


@app.route('/api/chora/amal_bajarish', methods=['POST'])
def chora_amal_bajarish():
    """Chora ko'rishdan tanlangan anketalar uchun tavsiya etilgan choraga
    qarab avtomatik amal bajaradi: xat yuborish YOKI Davo ariza tayyorlash
    (ommaviy) — MIB harakati alohida (bittadan) MIB bo'limida bajariladi."""
    data = request.get_json() or {}
    anketalar = data.get('anketalar', [])
    royxat = db.get_chora_korish_royxati()
    royxat_map = {r['portfel']['anketa_raqami']: r for r in royxat}

    xat_kerak, davo_kerak, boshqa = [], [], 0
    for anketa in anketalar:
        r = royxat_map.get(anketa)
        if not r:
            continue
        if r['chora'] in ('xat_yuborish', 'xat_yuborish_keyingi_bosqich'):
            xat_kerak.append(anketa)
        elif r['chora'] == 'davo_ariza_tayyorlash':
            davo_kerak.append(anketa)
        else:
            boshqa += 1

    if xat_kerak and davo_kerak:
        return jsonify({'xato': "Tanlanganlar orasida ham xat, ham Davo ariza bosqichidagilar bor. "
                                 "Iltimos, faqat bitta turdagi bosqichni tanlang."}), 400

    if xat_kerak:
        # talabnoma_xat_yaratish endpoint mantig'ini qayta ishlatamiz
        with app.test_request_context(json={'anketalar': xat_kerak}):
            resp = talabnoma_xat_yaratish()
        result = resp.get_json()
        return jsonify({'turi': 'xat', **result})

    if davo_kerak:
        with app.test_request_context(json={'anketalar': davo_kerak}):
            resp = davo_ariza_yaratish()
        result = resp.get_json()
        return jsonify({'turi': 'davo', **result})

    # Bitta mijoz, MIB bosqichida bo'lsa — MIB bo'limiga yo'naltirish kerakligini bildiramiz
    if len(anketalar) == 1:
        r = royxat_map.get(anketalar[0])
        if r and r['chora'] == 'mib_harakat_boshlash':
            return jsonify({'xato': "Bu mijoz MIB bosqichida. MIB harakati qo'shish uchun "
                                     "'MIB ijro harakatlari' bo'limiga o'ting va u yerdan "
                                     "'+ Harakat' tugmasini bosing."}), 400

    return jsonify({'xato': "Tanlanganlar orasida xat yoki Davo ariza bosqichidagi mijoz topilmadi "
                             "(MIB harakati alohida, MIB bo'limida bajariladi)."}), 400


@app.route('/api/chora/qidirish', methods=['GET'])
def chora_qidirish():
    anketa = request.args.get('anketa', '').strip()
    royxat = db.get_chora_korish_royxati()
    for i, r in enumerate(royxat):
        if r['portfel']['anketa_raqami'] == anketa:
            prow = r['portfel']
            jami = (prow.get('asosiy_qarz') or 0) + (prow.get('foiz_qarz') or 0) + (prow.get('jarima') or 0)
            return jsonify({'topildi': True, 'row': {
                'i': i, 'anketa_raqami': prow['anketa_raqami'], 'mijoz_nomi': prow['mijoz_nomi'],
                'turi': r['turi'], 'dpd': r['dpd'], 'qarzdorlik': jami,
                'chora': r['chora'], 'chora_nomi': r['chora_nomi'], 'tafsilot': r['tafsilot'],
            }})
    return jsonify({'topildi': False})


@app.route('/api/chora/excel_eksport', methods=['GET'])
def chora_excel_eksport():
    import tempfile
    from flask import send_file
    import pandas as pd
    royxat = db.get_chora_korish_royxati()
    rows = []
    for r in royxat:
        prow = r['portfel']
        jami = (prow.get('asosiy_qarz') or 0) + (prow.get('foiz_qarz') or 0) + (prow.get('jarima') or 0)
        rows.append({
            'Anketa raqami': prow['anketa_raqami'], 'Mijoz': prow['mijoz_nomi'], 'Turi': r['turi'],
            'DPD': r['dpd'], 'Qarzdorlik': jami, 'Chora': r['chora_nomi'], 'Tafsilot': r['tafsilot'],
        })
    df = pd.DataFrame(rows)
    with tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False) as tmp:
        tmp_path = tmp.name
    df.to_excel(tmp_path, index=False)
    return send_file(tmp_path, as_attachment=True, download_name='chora_korish.xlsx')


# ---------------------------------------------------------------------------
# 95413
# ---------------------------------------------------------------------------
@app.route('/api/nazorat95413/royxat', methods=['GET'])
def nazorat95413_royxat():
    royxat = db.get_95413_royxati()
    natija = []
    for r in royxat:
        prow = r['portfel']
        turi = util.turi_kodidan(prow.get('mijoz_turi_kodi'), prow.get('mijoz_turi'))
        natija.append({
            'anketa_raqami': prow['anketa_raqami'], 'mijoz_nomi': prow['mijoz_nomi'], 'turi': turi,
            'balans_95413': prow.get('balans_95413') or 0,
            'bosqich': r['bosqich'], 'bosqich_nomi': db.BOSQICH_NOMLARI_95413.get(r['bosqich'], r['bosqich']),
            'tafsilot': r['tafsilot'],
        })
    soni = {}
    for r in royxat:
        soni[r['bosqich']] = soni.get(r['bosqich'], 0) + 1
    jami_balans = sum((r['portfel'].get('balans_95413') or 0) for r in royxat)
    return jsonify({'royxat': natija, 'soni': soni, 'jami': len(royxat), 'jami_balans': jami_balans,
                     'bosqich_nomlari': db.BOSQICH_NOMLARI_95413})


@app.route('/api/nazorat95413/excel_eksport', methods=['GET'])
def nazorat95413_excel_eksport():
    import tempfile
    from flask import send_file
    import pandas as pd
    royxat = db.get_95413_royxati()
    rows = []
    for r in royxat:
        prow = r['portfel']
        turi = util.turi_kodidan(prow.get('mijoz_turi_kodi'), prow.get('mijoz_turi'))
        rows.append({
            'Anketa raqami': prow['anketa_raqami'], 'Mijoz': prow['mijoz_nomi'], 'Turi': turi,
            'Balans 95413': prow.get('balans_95413') or 0,
            'Bosqich': db.BOSQICH_NOMLARI_95413.get(r['bosqich'], r['bosqich']), 'Tafsilot': r['tafsilot'],
        })
    df = pd.DataFrame(rows)
    with tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False) as tmp:
        tmp_path = tmp.name
    df.to_excel(tmp_path, index=False)
    return send_file(tmp_path, as_attachment=True, download_name='95413_nazorati.xlsx')


@app.route('/api/nazorat95413/xat_yuborildi_belgilash', methods=['POST'])
def nazorat95413_xat_yuborildi_belgilash():
    data = request.get_json() or {}
    anketa = data.get('anketa_raqami', '').strip()
    conn = db.get_conn()
    row = conn.execute("SELECT id FROM xatlar WHERE anketa_raqami=?", (anketa,)).fetchone()
    conn.close()
    if not row:
        return jsonify({'xato': 'Xat topilmadi'}), 404
    db.mark_xat_yuborildi(row['id'])
    return jsonify({'ok': True})


@app.route('/api/nazorat95413/eski_ish_qidirish', methods=['GET'])
def nazorat95413_eski_ish_qidirish():
    anketa = request.args.get('anketa', '').strip()
    rows = db.get_portfel_by_anketa(anketa)
    if not rows:
        return jsonify({'topildi': False})
    prow = rows[0]
    turi, mijoz = util.resolve_mijoz(prow)
    jami = (prow.get('asosiy_qarz') or 0) + (prow.get('foiz_qarz') or 0) + (prow.get('jarima') or 0)
    return jsonify({'topildi': True, 'mijoz_nomi': prow.get('mijoz_nomi', ''), 'turi': turi,
                     'jami_qarz': jami, 'balans_95413': prow.get('balans_95413') or 0})


@app.route('/api/nazorat95413/eski_ish_kiritish', methods=['POST'])
def nazorat95413_eski_ish_kiritish():
    data = request.get_json() or {}
    anketa = data.get('anketa_raqami', '').strip()
    ish_raqami = data.get('ish_raqami', '').strip()
    sana = data.get('sana', '').strip()
    if not anketa or not ish_raqami or not sana:
        return jsonify({'xato': 'anketa_raqami, ish_raqami va sana kerak'}), 400
    rows = db.get_portfel_by_anketa(anketa)
    if not rows:
        return jsonify({'xato': 'Bu anketa portfelda topilmadi'}), 404
    prow = rows[0]
    mijoz_turi = util.turi_kodidan(prow.get('mijoz_turi_kodi'), prow.get('mijoz_turi'))
    qarz = data.get('qarzdorlik')
    qarz = float(qarz) if qarz else None

    xat_id = db.create_legacy_mib_xat(
        portfel_id=prow['id'], anketa_raqami=anketa, mijoz_nomi=prow.get('mijoz_nomi', ''),
        mijoz_turi=mijoz_turi, mib_ish_raqami=ish_raqami, mib_sana=sana,
        sud_ish_raqami=data.get('sud_ish_raqami') or None, joriy_qarzdorlik=qarz,
    )

    # MUHIM: 95413 uchun yig'ma jild ODDIY MIB jildlaridan ALOHIDA papkaga yoziladi —
    # bu kreditlar asosiy balansdan chiqarilgani uchun boshqa bo'limlarda ko'rinmasligi
    # mumkin, shu bois hujjatlari ham alohida saqlanadi.
    try:
        turi_m, mijoz = util.resolve_mijoz(prow)
        settings = db.get_all_settings()
        xat_yangilangan = db.get_xat_by_id(xat_id)
        papka_nomi = f"{letters.safe_filename(prow.get('mijoz_nomi', ''))}_{letters.safe_filename(anketa)}_{letters.safe_filename(ish_raqami)}"
        jild_papka = os.path.join(hujjatlar_papkasi(), 'Yigma_jildlar_95413', papka_nomi)
        os.makedirs(jild_papka, exist_ok=True)
        titul_path = os.path.join(jild_papka, '00_Titul.docx')
        letters.generate_yigma_jild_titul(titul_path, xat_yangilangan, prow, mijoz, settings)
        db.mark_yigma_jild_yaratildi(xat_id, jild_papka, titul_path)
    except Exception as e:
        return jsonify({'ok': True, 'ogohlantirish': f"Eski ish kiritildi, lekin yig'ma jild yaratishda xato: {e}"})

    db.add_mib_amal(xat_id, 'eski_ish_kiritildi', sana,
                     tavsif=f"95413 balansidagi eski ish (MIB ish raqami: {ish_raqami}) tizimga qo'lda kiritildi.")
    return jsonify({'ok': True})


# ---------------------------------------------------------------------------
# TAHLIL
# ---------------------------------------------------------------------------
@app.route('/api/tahlil/umumiy', methods=['GET'])
def tahlil_umumiy():
    return jsonify(db.get_umumiy_tahlil())


@app.route('/api/tahlil/tarix', methods=['GET'])
def tahlil_tarix():
    return jsonify({'tarix': db.get_tahlil_tarixi()})


@app.route('/api/tahlil/tarmoq_mijozlari', methods=['GET'])
def tahlil_tarmoq_mijozlari():
    tarmoq = request.args.get('tarmoq', '').strip()
    conn = db.get_conn()
    if tarmoq == "Noma'lum":
        rows = conn.execute(
            "SELECT anketa_raqami, mijoz_nomi, mijoz_turi, ead, stage FROM portfel "
            "WHERE faol=1 AND (tarmoq IS NULL OR TRIM(tarmoq)='') ORDER BY ead DESC LIMIT 300").fetchall()
    else:
        rows = conn.execute(
            "SELECT anketa_raqami, mijoz_nomi, mijoz_turi, ead, stage FROM portfel "
            "WHERE faol=1 AND TRIM(tarmoq)=? ORDER BY ead DESC LIMIT 300", (tarmoq,)).fetchall()
    conn.close()
    return jsonify({'mijozlar': [dict(r) for r in rows]})


@app.route('/api/tahlil/hisobot_yuklab_olish', methods=['GET'])
def tahlil_hisobot_yuklab_olish():
    import tempfile
    from flask import send_file
    tahlil = db.get_umumiy_tahlil()
    settings = db.get_all_settings()
    formatv = request.args.get('format', 'word')
    with tempfile.NamedTemporaryFile(suffix='.docx', delete=False) as tmp:
        tmp_path = tmp.name
    letters.generate_tahlil_hisoboti(tmp_path, tahlil, settings)
    if formatv == 'pdf':
        try:
            tmp_path = letters.convert_docx_to_pdf(tmp_path, delete_docx=True)
            return send_file(tmp_path, as_attachment=True, download_name='tahlil_hisoboti.pdf')
        except Exception as e:
            return jsonify({'xato': f"PDF'ga o'tkazishda xato (MS Word talab qilinadi): {e}"}), 400
    return send_file(tmp_path, as_attachment=True, download_name='tahlil_hisoboti.docx')


@app.route('/api/mib/harakatsizlar', methods=['GET'])
def mib_harakatsizlar():
    xatlar = db.get_mib_harakatsizlar()
    natija = []
    for x in xatlar:
        prow = db.get_portfel_by_id(x['portfel_id'])
        jami = (prow.get('asosiy_qarz') or 0) + (prow.get('foiz_qarz') or 0) + (prow.get('jarima') or 0) if prow else 0
        natija.append({
            'anketa_raqami': x['anketa_raqami'], 'mijoz_nomi': x['mijoz_nomi'], 'turi': x['mijoz_turi'],
            'jami_qarz': jami, 'mib_ish_raqami': x.get('mib_ish_raqami', ''),
            'harakatsizlik_kun': x.get('harakatsizlik_kun', ''),
        })
    return jsonify({'royxat': natija})


@app.route('/api/mib/harakatsizlar_excel', methods=['GET'])
def mib_harakatsizlar_excel():
    import tempfile
    from flask import send_file
    import pandas as pd
    xatlar = db.get_mib_harakatsizlar()
    if not xatlar:
        return jsonify({'xato': "Hozircha harakatsiz qolgan MIB ishi yo'q"}), 400
    rows = []
    for x in xatlar:
        prow = db.get_portfel_by_id(x['portfel_id'])
        jami = (prow.get('asosiy_qarz') or 0) + (prow.get('foiz_qarz') or 0) + (prow.get('jarima') or 0) if prow else 0
        rows.append({
            'Anketa raqami': x['anketa_raqami'], 'Mijoz': x['mijoz_nomi'], 'Turi': x['mijoz_turi'],
            "Qarzdorlik (so'm)": jami, 'MIB ish raqami': x.get('mib_ish_raqami', '') or '',
            'Necha kun harakatsiz': x.get('harakatsizlik_kun', ''),
        })
    df = pd.DataFrame(rows)
    with tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False) as tmp:
        tmp_path = tmp.name
    df.to_excel(tmp_path, index=False)
    return send_file(tmp_path, as_attachment=True, download_name='mib_harakatsiz_qolganlar.xlsx')


@app.route('/api/davo-ariza/hisobot', methods=['GET'])
def davo_ariza_hisobot():
    muddat_kun = int(db.get_all_settings().get('davo_ariza_muddati_kun', 5))
    sabab_nomlari = {'qarz_yopilgan': "Qarz to'liq yopilgan", 'mijoz_arizasi': "Mijozning yozma arizasi asosida",
                      'xodim_iltimosi': "Bank xodimi iltimosiga asosan"}
    natija = []
    for x in db.get_davo_ariza_hisoboti():
        prow = db.get_portfel_by_id(x['portfel_id'])
        jami_joriy = (prow.get('asosiy_qarz') or 0) + (prow.get('foiz_qarz') or 0) + (prow.get('jarima') or 0) if prow else 0
        davo_asosiy = x.get('davo_summasi_asosiy') or 0
        davo_foiz = x.get('davo_summasi_foiz') or 0
        davo_jarima = x.get('davo_summasi_jarima') or 0

        olib_kelindi = x.get('davo_ariza_holati') == 'olib_kelindi'
        yaratilgan, davo_dt = '', None
        try:
            davo_dt = datetime.datetime.fromisoformat(x['davo_ariza_sana'])
            yaratilgan = davo_dt.strftime('%d.%m.%Y')
        except Exception:
            yaratilgan = x.get('davo_ariza_sana', '') or ''

        holat_pill = 'yoq'
        if olib_kelindi and davo_dt:
            try:
                imzo_dt = datetime.datetime.strptime(x['davo_ariza_imzo_sana'], '%d.%m.%Y')
                kutilgan_kun = max((imzo_dt.date() - davo_dt.date()).days, 0)
            except Exception:
                kutilgan_kun = ''
            holati = "✓ Olib kelindi"
            holat_pill = 'olib_kelindi'
        elif davo_dt:
            kutilgan_kun = (datetime.datetime.now() - davo_dt).days
            if kutilgan_kun > muddat_kun:
                holati = f"⚠ Muddati o'tgan ({kutilgan_kun} kun)"
                holat_pill = 'otgan'
            else:
                holati = f"Kutilmoqda ({muddat_kun - kutilgan_kun} kun qoldi)"
                holat_pill = 'tayyor'
        else:
            kutilgan_kun, holati = '', ''

        sud_topshirildi = x.get('sud_holati') == 'topshirildi'
        sud_kiritilmadi_sababi = x.get('sud_kiritilmadi_sababi')
        if sud_topshirildi:
            sud_holati_matn = "✓ Sudga kiritildi"
        elif sud_kiritilmadi_sababi:
            izoh_qoshimcha = ''
            if sud_kiritilmadi_sababi == 'xodim_iltimosi':
                izoh_qoshimcha = f" ({x.get('sud_kiritilmadi_xodim_ism', '')} — {x.get('sud_kiritilmadi_izoh', '')})"
            sud_holati_matn = f"✕ Sudga kiritilmadi: {sabab_nomlari.get(sud_kiritilmadi_sababi, sud_kiritilmadi_sababi)}{izoh_qoshimcha}"
        elif olib_kelindi:
            sud_holati_matn = "Sudga jo'natish kutilmoqda"
        else:
            sud_holati_matn = "—"

        natija.append({
            'anketa_raqami': x['anketa_raqami'], 'mijoz_nomi': x['mijoz_nomi'], 'mijoz_turi': x['mijoz_turi'],
            'jami_qarz': jami_joriy,
            'davo_summasi_asosiy': davo_asosiy, 'davo_summasi_foiz': davo_foiz, 'davo_summasi_jarima': davo_jarima,
            'davo_summasi_jami': davo_asosiy + davo_foiz + davo_jarima,
            'yaratilgan': yaratilgan, 'kutilgan_kun': kutilgan_kun,
            'ish_raqami': x.get('davo_ariza_ish_raqami', '') or '', 'imzo_sana': x.get('davo_ariza_imzo_sana', '') or '',
            'holati': holati, 'holat_pill': holat_pill,
            'sud_ish_raqami': x.get('sud_ish_raqami', '') or '', 'sud_sana': x.get('sud_topshirilgan_sana', '') or '',
            'sud_holati': sud_holati_matn,
        })
    return jsonify({'royxat': natija})


@app.route('/api/davo-ariza/hisobot_excel', methods=['GET'])
def davo_ariza_hisobot_excel():
    import tempfile
    from flask import send_file
    import pandas as pd
    resp = davo_ariza_hisobot()
    rows = resp.get_json()['royxat']
    if not rows:
        return jsonify({'xato': "Hali birorta Davo ariza yaratilmagan"}), 400
    excel_qatorlar = []
    for r in rows:
        excel_qatorlar.append({
            'Anketa raqami': r['anketa_raqami'], 'Mijoz': r['mijoz_nomi'], 'Turi': r['mijoz_turi'],
            'Ish raqami': r['ish_raqami'], 'Tayyorlangan sanasi': r['yaratilgan'],
            'Davo summasi (asosiy)': r['davo_summasi_asosiy'], 'Davo summasi (foiz)': r['davo_summasi_foiz'],
            'Davo summasi (penya)': r['davo_summasi_jarima'], 'Davo summasi (jami)': r['davo_summasi_jami'],
            "Joriy qarzdorlik (bugun)": r['jami_qarz'],
            'Holati (Palata/SSP)': r['holati'],
            'Sud ish raqami': r['sud_ish_raqami'], 'Sudga topshirilgan sana': r['sud_sana'],
            'Sudga jo\'natish holati / izoh': r['sud_holati'],
        })
    df = pd.DataFrame(excel_qatorlar)
    with tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False) as tmp:
        tmp_path = tmp.name
    df.to_excel(tmp_path, index=False)
    return send_file(tmp_path, as_attachment=True, download_name='davo_ariza_hisoboti.xlsx')


# ---------------------------------------------------------------------------
# VAFOT ETGANLAR
# ---------------------------------------------------------------------------
@app.route('/api/vafot/royxat', methods=['GET'])
def vafot_royxat():
    royxat = db.get_vafot_etganlar_royxati()
    return jsonify({'royxat': royxat})


@app.route('/api/vafot/qoshish', methods=['POST'])
def vafot_qoshish():
    data = request.get_json() or {}
    anketa = data.get('anketa_raqami', '').strip()
    vafot_sanasi = data.get('vafot_sanasi', '').strip()
    if not anketa or not vafot_sanasi:
        return jsonify({'xato': 'anketa_raqami va vafot_sanasi kerak'}), 400
    prow_list = db.get_portfel_by_anketa(anketa)
    mijoz_nomi = prow_list[0].get('mijoz_nomi', '') if prow_list else data.get('mijoz_nomi', '')
    conn = db.get_conn()
    conn.execute(
        "INSERT INTO vafot_etganlar (anketa_raqami, mijoz_nomi, vafot_sanasi, polis_holati) "
        "VALUES (?, ?, ?, 'amalda')", (anketa, mijoz_nomi, vafot_sanasi))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})


@app.route('/api/vafot/fayl_yuklash', methods=['POST'])
def vafot_fayl_yuklash():
    """Vafot etgan mijoz uchun hujjat (o'limlik guvohnomasi / pasport / sug'urta
    polis) yuklaydi va uni ko'rish/yuklab olish mumkin bo'lgan holatda saqlaydi."""
    vafot_id = request.form.get('id')
    turi = request.form.get('turi')  # olimlik | pasport | sugurta_polis
    maydon_map = {
        'olimlik': 'olimlik_guvohnomasi_fayl',
        'pasport': 'pasport_fayl',
        'sugurta_polis': 'sugurta_polis_fayl',
    }
    if not vafot_id or turi not in maydon_map:
        return jsonify({'xato': "id va to'g'ri turi (olimlik/pasport/sugurta_polis) kerak"}), 400
    f = request.files.get('file')
    if not f or not f.filename:
        return jsonify({'xato': 'Fayl yuborilmadi'}), 400

    conn = db.get_conn()
    row = conn.execute('SELECT anketa_raqami FROM vafot_etganlar WHERE id=?', (vafot_id,)).fetchone()
    if not row:
        conn.close()
        return jsonify({'xato': 'Yozuv topilmadi'}), 404
    anketa = row['anketa_raqami']
    out_dir = os.path.join(hujjatlar_papkasi(), 'vafot_etganlar', letters.safe_filename(anketa))
    os.makedirs(out_dir, exist_ok=True)
    fayl_yoli = os.path.join(out_dir, f"{turi}_{f.filename}")
    mustahkam_fayl_saqlash(f, fayl_yoli, ozbek_kengaytma_tekshiruvi=False)

    ustun = maydon_map[turi]
    conn.execute(f'UPDATE vafot_etganlar SET {ustun}=? WHERE id=?', (fayl_yoli, vafot_id))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})


@app.route('/api/vafot/yangilash', methods=['POST'])
def vafot_yangilash():
    data = request.get_json() or {}
    vafot_id = data.get('id')
    if not vafot_id:
        return jsonify({'xato': 'id kerak'}), 400
    ruxsat_etilgan = ['polis_holati', 'sugurta_kompaniya', 'sugurta_polis_raqam', 'xabarnoma_holati',
                       'xabarnoma_yuborilgan_sana', 'javob_kelgan_sana']
    updates = {k: v for k, v in data.items() if k in ruxsat_etilgan}
    if not updates:
        return jsonify({'xato': "Yangilanadigan maydon topilmadi"}), 400
    conn = db.get_conn()
    set_clause = ', '.join(f'{k}=?' for k in updates)
    conn.execute(f'UPDATE vafot_etganlar SET {set_clause} WHERE id=?', (*updates.values(), vafot_id))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})


@app.route('/api/vafot/sugurta_kiritish', methods=['POST'])
def vafot_sugurta_kiritish():
    vafot_id = request.form.get('id')
    kompaniya = request.form.get('kompaniya', '')
    raqam = request.form.get('raqam', '')
    if not vafot_id:
        return jsonify({'xato': 'id kerak'}), 400
    v = db.get_vafot_etgan_by_id(vafot_id)
    if not v:
        return jsonify({'xato': 'Yozuv topilmadi'}), 404
    if v['polis_holati'] != 'amalda':
        return jsonify({'xato': "Sug'urta polisi amalda emas — sug'urta ma'lumoti kiritilmaydi."}), 400
    polis_dest = None
    f = request.files.get('polis_fayl')
    if f and f.filename:
        out_dir = os.path.join(hujjatlar_papkasi(), 'vafot_etganlar', letters.safe_filename(v['anketa_raqami']))
        os.makedirs(out_dir, exist_ok=True)
        polis_dest = os.path.join(out_dir, f"Polis_{f.filename}")
        mustahkam_fayl_saqlash(f, polis_dest, ozbek_kengaytma_tekshiruvi=False)
    db.update_sugurta_malumot(vafot_id, kompaniya, raqam, polis_dest)
    return jsonify({'ok': True})


@app.route('/api/vafot/xabarnoma_tayyorlash', methods=['GET'])
def vafot_xabarnoma_tayyorlash():
    from flask import send_file
    vafot_id = request.args.get('id')
    v = db.get_vafot_etgan_by_id(vafot_id)
    if not v:
        return jsonify({'xato': 'Yozuv topilmadi'}), 404
    if v['polis_holati'] != 'amalda':
        return jsonify({'xato': "Sug'urta polisi amalda bo'lmagan mijoz uchun xabarnoma tayyorlanmaydi."}), 400
    if not v.get('sugurta_kompaniya'):
        return jsonify({'xato': "Avval sug'urta ma'lumotini kiriting."}), 400
    prow = db.get_portfel_by_id(v['portfel_id']) if v.get('portfel_id') else None
    if not prow:
        rows = db.get_portfel_by_anketa(v['anketa_raqami'])
        prow = rows[0] if rows else None
    if not prow:
        return jsonify({'xato': "Bog'liq portfel yozuvi topilmadi."}), 404
    turi_m, mijoz = util.resolve_mijoz(prow)
    settings = db.get_all_settings()
    out_dir = os.path.join(hujjatlar_papkasi(), 'vafot_etganlar', letters.safe_filename(v['anketa_raqami']))
    os.makedirs(out_dir, exist_ok=True)
    fname = f"Xabarnoma_{letters.safe_filename(v['anketa_raqami'])}_{letters.safe_filename(v['mijoz_nomi'])}.docx"
    out_path = os.path.join(out_dir, fname)
    try:
        letters.generate_sugurta_xabarnoma(out_path, v, prow, mijoz, settings)
    except Exception as e:
        return jsonify({'xato': f"Xabarnoma yaratishda xato: {e}"}), 400
    return send_file(out_path, as_attachment=True, download_name=fname)


@app.route('/api/vafot/xabarnoma_yuborildi', methods=['POST'])
def vafot_xabarnoma_yuborildi():
    data = request.get_json() or {}
    vafot_id = data.get('id')
    sana = data.get('sana', '').strip()
    if not vafot_id or not sana:
        return jsonify({'xato': 'id va sana kerak'}), 400
    db.mark_xabarnoma_yuborildi(vafot_id, sana)
    return jsonify({'ok': True})


@app.route('/api/vafot/javob_keldi', methods=['POST'])
def vafot_javob_keldi():
    vafot_id = request.form.get('id')
    sana = request.form.get('sana', '').strip()
    if not vafot_id or not sana:
        return jsonify({'xato': 'id va sana kerak'}), 400
    v = db.get_vafot_etgan_by_id(vafot_id)
    if not v:
        return jsonify({'xato': 'Yozuv topilmadi'}), 404
    if v['xabarnoma_holati'] != 'yuborildi':
        return jsonify({'xato': "Avval xabarnoma yuborilgani belgilanishi kerak."}), 400
    fayl_dest = None
    f = request.files.get('fayl')
    if f and f.filename:
        out_dir = os.path.join(hujjatlar_papkasi(), 'vafot_etganlar', letters.safe_filename(v['anketa_raqami']))
        os.makedirs(out_dir, exist_ok=True)
        fayl_dest = os.path.join(out_dir, f"Javob_{f.filename}")
        mustahkam_fayl_saqlash(f, fayl_dest, ozbek_kengaytma_tekshiruvi=False)
    db.mark_sugurta_javob_keldi(vafot_id, sana, fayl_dest)
    return jsonify({'ok': True})


@app.route('/api/fayl_korish', methods=['GET'])
def fayl_korish():
    """Har qanday yaratilgan/yuklangan hujjatni (Word, PDF va h.k.) ko'rsatish
    yoki yuklab olish uchun umumiy endpoint."""
    from flask import send_file
    yol = request.args.get('yol', '')
    if not yol or not os.path.isfile(yol):
        return jsonify({'xato': 'Fayl topilmadi'}), 404
    return send_file(yol, as_attachment=request.args.get('yuklab_olish') == '1')


# ---------------------------------------------------------------------------
# PORTFEL
# ---------------------------------------------------------------------------
@app.route('/api/portfel/import', methods=['POST'])
def portfel_import():
    import tempfile
    f = request.files.get('file')
    if not f:
        return jsonify({'xato': 'Fayl yuborilmadi'}), 400

    # MUHIM: f.save() o'rniga faylni to'liq xotiraga o'qib, aniq binary
    # rejimda, so'ng diskka mustahkam yozamiz (flush + fsync). Bu — ba'zi
    # Windows muhitlarida (masalan antivirus real-vaqt tekshiruvi fayl
    # yozilayotgan paytga to'g'ri kelib qolganda) faylning to'liq
    # yozilmasdan o'qilib qolishi ("incorrect header check" xatosi)
    # ehtimolini kamaytiradi.
    fayl_baytlari = f.read()
    if len(fayl_baytlari) < 100:
        return jsonify({'xato': "Yuklangan fayl juda kichik yoki bo'sh — qayta urinib ko'ring."}), 400
    # .xlsb (va .xlsx) fayllar aslida ZIP arxivi — to'g'ri fayl bo'lsa,
    # har doim 'PK' baytlari bilan boshlanishi kerak. Bu yerda xato
    # bo'lsa, foydalanuvchiga zlib'ning tushunarsiz xatosi o'rniga aniq,
    # tushunarli xabar ko'rsatamiz.
    if fayl_baytlari[:2] != b'PK':
        return jsonify({'xato': "Fayl to'liq yuklanmadi yoki buzilgan (ZIP formatiga mos emas). "
                                 "Iltimos, faylni qaytadan tanlab, qayta urinib ko'ring."}), 400

    with tempfile.NamedTemporaryFile(suffix='.xlsb', delete=False) as tmp:
        tmp.write(fayl_baytlari)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp_path = tmp.name
    try:
        result = importer.import_portfel_xlsb(tmp_path)
        conn = db.get_conn()
        faol_soni = conn.execute("SELECT COUNT(*) c FROM portfel WHERE faol=1").fetchone()['c']
        faolsiz_soni = conn.execute("SELECT COUNT(*) c FROM portfel WHERE faol=0").fetchone()['c']
        conn.close()
        # MUHIM: portfel har yangilanganda shu kungi tahlil ko'rsatkichlari
        # avtomatik "suratga olinadi" — vaqt o'tishi bilan Tahlil bo'limida
        # haqiqiy tendensiya grafigini chizish uchun.
        try:
            db.tahlil_snapshot_saqlash()
        except Exception:
            pass  # Snapshot muvaffaqiyatsiz bo'lsa ham, import natijasi baribir qaytadi
        return jsonify({'jami_qator': result['jami_qator'], 'sheet': result.get('sheet', ''),
                         'faol_soni': faol_soni, 'faolsiz_soni': faolsiz_soni})
    except Exception as e:
        xabar = str(e)
        if 'decompress' in xabar.lower() or 'header check' in xabar.lower():
            xabar = ("Fayl o'qishda ichki xato: yuklangan .xlsb fayl to'liq yoki to'g'ri "
                     "yuklanmadi (buzilgan bo'lishi mumkin). Iltimos: 1) antivirusni vaqtincha "
                     "o'chirib yoki dastur papkasiga istisno qo'shib qayta urinib ko'ring, "
                     "2) faylni qayta saqlab (Excel'da 'Saqlash') qayta yuklang. "
                     f"(texnik tafsilot: {xabar})")
        return jsonify({'xato': xabar}), 400
    finally:
        os.remove(tmp_path)


@app.route('/api/portfel/royxat', methods=['GET'])
def portfel_royxat():
    rows = db.get_portfel_45_kun(45)
    natija = []
    for r in rows[:500]:
        turi, mijoz = util.resolve_mijoz(r)
        jami = (r.get('asosiy_qarz') or 0) + (r.get('foiz_qarz') or 0) + (r.get('jarima') or 0)
        natija.append({
            'anketa_raqami': r['anketa_raqami'], 'mijoz_nomi': r['mijoz_nomi'], 'turi': turi,
            'dpd': r.get('dpd_max', 0), 'jami_qarz': jami,
        })
    return jsonify({'royxat': natija, 'jami': len(rows)})


# ---------------------------------------------------------------------------
# MIJOZLAR BAZASI
# ---------------------------------------------------------------------------
@app.route('/api/mijozlar/stats', methods=['GET'])
def mijozlar_stats():
    conn = db.get_conn()
    jis = conn.execute("SELECT COUNT(*) c FROM mijozlar WHERE turi='jismoniy'").fetchone()['c']
    yur = conn.execute("SELECT COUNT(*) c FROM mijozlar WHERE turi='yuridik'").fetchone()['c']
    conn.close()
    return jsonify({'jismoniy': jis, 'yuridik': yur})


@app.route('/api/mijozlar/import_txt', methods=['POST'])
def mijozlar_import_txt():
    import tempfile
    f = request.files.get('file')
    if not f:
        return jsonify({'xato': 'Fayl yuborilmadi'}), 400
    suffix = '.zip' if f.filename.lower().endswith('.zip') else '.txt'
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp_path = tmp.name
    # .txt fayllar ZIP tuzilishida bo'lmagani uchun, faqat .zip bo'lsa PK
    # header tekshiruvini yoqamiz.
    xato_natija = mustahkam_fayl_saqlash(f, tmp_path, ozbek_kengaytma_tekshiruvi=(suffix == '.zip'))
    if xato_natija:
        return jsonify({'xato': xato_natija[0]}), xato_natija[1]
    try:
        result = importer.import_clients_txt(tmp_path)
        return jsonify(result)
    except Exception as e:
        return jsonify({'xato': str(e)}), 400
    finally:
        os.remove(tmp_path)


@app.route('/api/mijozlar/excel_ustunlari', methods=['POST'])
def mijozlar_excel_ustunlari():
    import tempfile
    f = request.files.get('file')
    if not f:
        return jsonify({'xato': 'Fayl yuborilmadi'}), 400
    with tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False) as tmp:

        tmp_path = tmp.name

    xato_natija = mustahkam_fayl_saqlash(f, tmp_path)

    if xato_natija:

        return jsonify({'xato': xato_natija[0]}), xato_natija[1]
    try:
        cols, preview_df = importer.preview_mijozlar_columns(tmp_path)
        # Faylni vaqtincha saqlab qo'yamiz — keyingi (haqiqiy import) so'rovida qayta ishlatiladi
        doimiy_yol = os.path.join(hujjatlar_papkasi(), '_vaqtinchalik_mijozlar_excel.xlsx')
        os.makedirs(os.path.dirname(doimiy_yol), exist_ok=True)
        import shutil
        shutil.copy2(tmp_path, doimiy_yol)
        return jsonify({'ustunlar': cols, 'namuna': preview_df.fillna('').astype(str).values.tolist()})
    except Exception as e:
        return jsonify({'xato': str(e)}), 400
    finally:
        os.remove(tmp_path)


@app.route('/api/mijozlar/excel_import', methods=['POST'])
def mijozlar_excel_import():
    data = request.get_json() or {}
    turi = data.get('turi')
    mapping = data.get('mapping', {})
    if not mapping.get('kalit') or not mapping.get('ism'):
        return jsonify({'xato': "Bog'lovchi ID va Ism ustunlari majburiy"}), 400
    fayl_yoli = os.path.join(hujjatlar_papkasi(), '_vaqtinchalik_mijozlar_excel.xlsx')
    if not os.path.exists(fayl_yoli):
        return jsonify({'xato': "Avval faylni yuklang (ustunlarni tanlash bosqichi)"}), 400
    try:
        result = importer.import_mijozlar_xlsx(fayl_yoli, turi, mapping)
        return jsonify(result)
    except Exception as e:
        return jsonify({'xato': str(e)}), 400
    finally:
        try:
            os.remove(fayl_yoli)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# REJA GRAFIK
# ---------------------------------------------------------------------------
@app.route('/api/reja/kunlik', methods=['GET'])
def reja_kunlik():
    return jsonify(db.get_ish_kuni_rejasi())


@app.route('/api/reja/tarmoq', methods=['GET'])
def reja_tarmoq():
    conn = db.get_conn()
    rows = conn.execute('''
        SELECT COALESCE(NULLIF(TRIM(p.tarmoq), ''), "Noma'lum") AS tarmoq,
               COUNT(DISTINCT x.id) AS xat_soni,
               SUM(CASE WHEN x.holat IN ('yuborildi','muddati_otgan') THEN 1 ELSE 0 END) AS yuborilgan_soni,
               SUM(CASE WHEN x.davo_ariza_fayl_yoli IS NOT NULL THEN 1 ELSE 0 END) AS davo_soni,
               SUM(CASE WHEN x.sud_holati='topshirildi' THEN 1 ELSE 0 END) AS sud_soni,
               SUM(CASE WHEN x.mib_holati='otkazildi' THEN 1 ELSE 0 END) AS mib_soni
        FROM xatlar x
        LEFT JOIN portfel p ON p.id = x.portfel_id
        GROUP BY tarmoq ORDER BY xat_soni DESC
    ''').fetchall()
    conn.close()
    return jsonify({'tarmoq': [dict(r) for r in rows]})


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    app.run(host='127.0.0.1', port=port, debug=False)
