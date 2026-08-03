// 读取 Tampermonkey 存储中的脚本源码（临时调试用）
const { DatabaseSync } = require('node:sqlite');

const DB_PATH = process.argv[2];
const db = new DatabaseSync(DB_PATH, { readOnly: true });

// 列出表
const tables = db.prepare("SELECT name FROM sqlite_master WHERE type='table'").all();
console.log('TABLES:', JSON.stringify(tables));

// localStorage 通常是 key/value 结构
for (const t of tables) {
    try {
        const cols = db.prepare(`PRAGMA table_info(${t.name})`).all();
        console.log(`\n=== ${t.name} ===`);
        console.log('COLUMNS:', JSON.stringify(cols.map(c => c.name)));
        const rows = db.prepare(`SELECT * FROM ${t.name} LIMIT 5`).all();
        for (const r of rows) {
            console.log('ROW:', JSON.stringify(r).slice(0, 500));
        }
    } catch (e) {
        console.log(`\n=== ${t.name} ERROR: ${e.message}`);
    }
}
db.close();
