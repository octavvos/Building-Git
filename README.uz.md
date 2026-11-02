# wyag — Python'da Git

[![tests](https://github.com/octavvos/Building-Git/actions/workflows/tests.yml/badge.svg)](https://github.com/octavvos/Building-Git/actions/workflows/tests.yml)
![coverage](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/octavvos/Building-Git/badges/coverage.json)
![python](https://img.shields.io/badge/python-3.9%2B-blue)
[![license](https://img.shields.io/badge/license-MIT-lightgrey)](LICENSE)

[English](README.md) | **O'zbekcha**

Git'ning asosiy qismi sof Python'da noldan yozilgan. Maqsad: Git tarixni qanday saqlashi
va o'qishini ichidan tushunish. Loyiha [Write yourself a Git!](https://wyag.thb.lt/)
qo'llanmasidan boshlangan va undan ham uzoqqa ketgan: packfile'lar, binar index,
`.gitignore` va `HEAD~2^{tree}` kabi revision sintaksisi.

wyag Git bilan bir xil disk formatida o'qiydi va yozadi, shuning uchun haqiqiy
repozitoriylarda, jumladan yangi klon qilinganlarida ham ishlaydi. Testlar har bir
buyruqni haqiqiy `git` bilan yonma-yon ishlatib, natijalar bayt-baytigacha bir xil
ekanini tekshiradi. wyag yaratgan commit va tag'lar git yaratadiganlari bilan **bir xil
hash**ga ega va `git fsck --strict` tekshiruvidan o'tadi.

## Buyruqlar

| Buyruq | Vazifasi |
|---|---|
| `init [papka]` | Bo'sh repozitoriy yaratadi |
| `add [-f] yo'l...` | Fayl yoki papkalarni index'ga qo'shadi (`.gitignore`ni hisobga oladi, o'chirilganlarni ham) |
| `rm [--cached] [-r] yo'l...` | Fayllarni index'dan va diskdan o'chiradi; saqlanmagan o'zgarishlarni o'chirmaydi |
| `commit -m xabar` | Index'ni commit qiladi va joriy branch'ni suradi |
| `status [-s]` | O'zgarishlar holati; `-s` natijasi `git status --porcelain` bilan bir xil |
| `ls-files [-s]` | Index tarkibi |
| `log [-n N] [--oneline] [rev]` | Tarix, merge'lar bilan, eng yangisi birinchi |
| `ls-tree [-r] rev` | Tree tarkibi |
| `cat-file [-t] rev` | Obyekt tarkibi yoki turi |
| `hash-object [-w] fayl` | Fayl hash'i, `-w` bilan saqlaydi |
| `checkout rev papka` | Commit fayllarini bo'sh papkaga yozadi |
| `rev-parse [--short] rev...` | Revision'larni hash'ga aylantiradi |
| `show-ref [--heads] [--tags]` | Ref'lar ro'yxati |
| `tag [-a] [-m xabar] [nom [rev]]` | Tag'lar ro'yxati yoki yangi tag (oddiy yoki annotated) |

`rev` sifatida to'liq yoki qisqa hash, `HEAD`, branch, tag, yoki ulardan keyin `~N`,
`^N`, `^{tur}` ishlatish mumkin. Masalan: `HEAD~3`, `HEAD^2` (merge'ning ikkinchi
ota-commit'i), `v1.0^{tree}`.

## Ishga tushirish

Python 3.9+ kerak, tashqi kutubxonalar yo'q.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

wyag init demo && cd demo
echo "salom" > salom.txt
wyag add .
wyag commit -m "Birinchi commit"
wyag log

# Istalgan mavjud git repozitoriyda ham ishlaydi
cd ~/biror-loyiha
wyag log --oneline -n 5
wyag status
```

Muallif ma'lumotini wyag git config'dagi `user.name` / `user.email`dan yoki
`GIT_AUTHOR_*` / `GIT_COMMITTER_*` muhit o'zgaruvchilaridan oladi, xuddi git kabi.

## Qanday ishlaydi

**Obyektlar.** Git obyektlarni tarkibiga qarab saqlaydi. Har bir obyekt
`<tur> <hajm>\0<tarkib>` ko'rinishida zlib bilan siqiladi va SHA-1 bilan nomlanadi.
Blob — fayl tarkibi. Tree — papka ro'yxati, har bir yozuv `<mode> <nom>\0<20 baytli sha>`
(papka nomlari `/` bilan tugagandek tartiblanadi, aks holda hash noto'g'ri chiqadi).
Commit va tag — `kalit qiymat` qatorlari, bo'sh qator va xabar.

**Packfile'lar.** `git gc` va `git clone` obyektlarni bitta faylga yig'adi, ularning
ko'pi *delta* ko'rinishida saqlanadi: boshqa obyektga nisbatan "nusxa ol / qo'sh"
ko'rsatmalari. wyag `.idx` faylini o'qiydi (v2, 2 GiB'dan katta pack'lar uchun 64 bitli
offset jadvali bilan), obyektni topadi, `OFS_DELTA` / `REF_DELTA` zanjirini to'liq
obyektgacha kuzatib, ko'rsatmalarni qayta bajaradi.

**Index.** `.git/index` — har bir staged fayl, uning blob hash'i, mode'i va `stat()`
ma'lumotlari yozilgan binar fayl. `status` o'zgarmagan fayllarni qayta hash qilmaslik
uchun `stat()` ma'lumotidan foydalanadi. Lekin index yozilgan soniyaning o'zida
o'zgartirilgan fayllarni baribir qayta hash qiladi ("racy git"), chunki vaqt belgisi
bunday o'zgarishni ajrata olmaydi. Index git kabi `index.lock` orqali atomik yoziladi.

**Commit.** `commit` tekis index'dan ichma-ich tree obyektlarini yig'adi (eng chuqur
papkalardan boshlab), keyin root tree va oldingi `HEAD`ga ishora qiluvchi commit yozadi.

**Tarix.** `log` commit grafini vaqt bo'yicha tartiblangan navbat (priority queue) bilan
aylanib chiqadi, shuning uchun merge'li tarixni `git log` bilan bir xil tartibda chiqaradi.

## Tuzilishi

```
wyag/
├── __main__.py     # CLI: argumentlar va natijani chiqarish
├── repository.py   # GitRepository, init, repozitoriyni topish
├── objects.py      # Blob, commit, tree, tag; o'qish/yozish; revision sintaksisi
├── pack.py         # Packfile va .idx o'qish, delta'larni ochish
├── refs.py         # HEAD, branch, tag, packed-refs, ref nomi qoidalari
├── index.py        # .git/index o'qish va yozish (v2 va v3)
├── ignore.py       # .gitignore, info/exclude va core.excludesFile qoidalari
├── worktree.py     # status, add, rm, commit
└── config.py       # git config va muallif ma'lumoti
tests/              # ~180 ta test, ko'pi haqiqiy git bilan solishtiradi
```

## Testlar

```bash
pytest       # coverage bilan ishlaydi, 90% dan past bo'lsa xato beradi
ruff check .
```

Testlar haqiqiy `git` bilan repozitoriylar yaratadi (merge, annotated tag, symlink,
bajariladigan fayllar, chuqur delta zanjirlari, merge konfliktlari, submodule yozuvlari)
va wyag natijalari hamda hash'larini git'niki bilan solishtiradi. `.gitignore` qoidalari
`git check-ignore` bilan tekshiriladi. CI hammasini Python 3.9, 3.11 va 3.13
versiyalarida ishga tushiradi.

## Cheklovlar

- `branch`, `switch`, `merge`, `diff`, `fetch` va `push` hali yo'q. `checkout` faqat
  bo'sh papkaga nusxa yozadi.
- `status` qayta nomlashlarni aniqlamaydi va har qanday konfliktni `UU` deb ko'rsatadi.
- Index'ning 4-versiyasi va split index qo'llab-quvvatlanmaydi (git ularni odatda
  ishlatmaydi).
- Faqat SHA-1 repozitoriylar; commit xabari faqat `-m` orqali beriladi.

## Litsenziya

[MIT](LICENSE)
