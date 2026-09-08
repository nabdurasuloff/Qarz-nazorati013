// Qarz Nazorat — Electron asosiy jarayoni.
// Bu fayl: (1) Python backend serverni fon jarayon sifatida ishga
// tushiradi, (2) asosiy oynani ochadi, (3) dastur yopilganda backend'ni
// ham to'xtatadi.

const { app, BrowserWindow, ipcMain, shell } = require('electron');
const path = require('path');
const { spawn } = require('child_process');

const isDev = !app.isPackaged;
const BACKEND_PORT = 8877;

let backendProcess = null;
let mainWindow = null;

function backendYolini_topish() {
    if (isDev) {
        // Ishlab chiqish rejimida — tizimdagi python3 orqali to'g'ridan-to'g'ri
        return {
            komanda: 'python3',
            argumentlar: [path.join(__dirname, '..', 'backend', 'server.py')],
        };
    }
    // Qadoqlangan (.exe) holatda — PyInstaller bilan yig'ilgan backend.exe
    return {
        komanda: path.join(process.resourcesPath, 'backend', 'backend.exe'),
        argumentlar: [],
    };
}

function backendniIshgaTushirish() {
    const { komanda, argumentlar } = backendYolini_topish();
    backendProcess = spawn(komanda, argumentlar, {
        env: { ...process.env, PORT: String(BACKEND_PORT) },
        windowsHide: true, // Windows'da backend.exe konsol oynasi ko'rinmasin
    });
    backendProcess.stdout.on('data', (d) => console.log(`[backend] ${d}`));
    backendProcess.stderr.on('data', (d) => console.error(`[backend xato] ${d}`));
}

function oynaniOchish() {
    mainWindow = new BrowserWindow({
        width: 1450,
        height: 900,
        minWidth: 1100,
        minHeight: 700,
        backgroundColor: '#F4F6FB',
        webPreferences: {
            preload: path.join(__dirname, 'preload.js'),
            contextIsolation: true,
            nodeIntegration: false,
        },
        title: 'Qarz Nazorat va Talabnoma Tizimi',
    });
    mainWindow.loadFile(path.join(__dirname, 'index.html'));

    // MUHIM TUZATISH: renderer.js ko'p joyda window.open(url, '_blank') orqali
    // fayl yuklab olish/ko'rish (Excel, PDF, Word shablonlar) uchun murojaat
    // qiladi. Bu sozlama bo'lmasa, Electron har safar BO'SH, buzilgan yangi
    // ichki oyna ochib, klaviatura e'tiborini o'ziga tortib oladi — natijada
    // asosiy oynada matn kiritib bo'lmay qoladi. Endi bunday so'rovlar
    // o'rniga, fayl operatsion tizimning standart brauzerida ochiladi
    // (yuklab olish/ko'rish uchun to'g'ri va xavfsiz yo'l).
    mainWindow.webContents.setWindowOpenHandler(({ url }) => {
        shell.openExternal(url);
        return { action: 'deny' };
    });

    if (isDev) {
        // mainWindow.webContents.openDevTools();
    }
}

app.whenReady().then(() => {
    backendniIshgaTushirish();
    setTimeout(oynaniOchish, 800); // backend ishga tushishi uchun kichik kutish
});

app.on('window-all-closed', () => {
    if (backendProcess) backendProcess.kill();
    if (process.platform !== 'darwin') app.quit();
});

ipcMain.handle('backend-port', () => BACKEND_PORT);
