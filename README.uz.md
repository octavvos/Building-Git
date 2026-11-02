# wyag — Python'da Git

[![tests](https://github.com/octavvos/Building-Git/actions/workflows/tests.yml/badge.svg)](https://github.com/octavvos/Building-Git/actions/workflows/tests.yml)
![coverage](https://img.shields.io/badge/coverage-96%25-brightgreen)
![python](https://img.shields.io/badge/python-3.9%2B-blue)
[![license](https://img.shields.io/badge/license-MIT-lightgrey)](LICENSE)

[English](README.md) | **O'zbekcha**

Git'ning asosiy qismi sof Python'da noldan yozilgan. Maqsad: Git tarixni qanday saqlashi
va o'qishini ichidan tushunish. Loyiha [Write yourself a Git!](https://wyag.thb.lt/)
qo'llanmasi asosida qurilgan.

wyag Git bilan bir xil disk formatida o'qiydi va yozadi, shuning uchun haqiqiy
repozitoriylarda ishlaydi. Uning `log`, `ls-tree` va `cat-file` natijalari `git` bilan
bayt-baytigacha bir xil ekani testlarda tekshiriladi. wyag yozgan commit'lar
`git fsck --strict` tekshiruvidan o'tadi.

## Buyruqlar

| Buyruq | Vazifasi |
|---|---|
| `init [papka]` | Bo'sh repozitoriy yaratadi (`.git`, `HEAD`, `config`, `objects/`, `refs/`) |
| `hash-object [-w] fayl` | Fayl hash'ini hisoblaydi, `-w` bilan uni `.git/objects`ga yozadi |
| `cat-file [-t] obyekt` | Obyekt tarkibini, `-t` bilan esa turini chiqaradi |
| `log [-n N] [--oneline] [commit]` | Tarixni ko'rsatadi, merge'lar bilan birga, eng yangisi birinchi |
| `ls-tree [-r] tree-ish` | Tree tarkibini chiqaradi, `-r` ichki papkalarga ham kiradi |
| `checkout commit papka` | Commit fayllarini bo'sh papkaga yozadi |

Obyekt kerak bo'lgan joyda to'liq yoki qisqa hash, `HEAD`, branch yoki tag nomini
ishlatish mumkin.

## Ishga tushirish

Python 3.9+ kerak, tashqi kutubxonalar yo'q.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Istalgan git repozitoriyda ishlatish mumkin
cd ~/biror-loyiha
wyag log --oneline -n 5
wyag ls-tree -r HEAD
wyag checkout HEAD /tmp/nusxa
```

## Qanday ishlaydi

Git obyektlarni tarkibiga qarab saqlaydi. Har bir obyekt `<tur> <hajm>\0<tarkib>`
ko'rinishida zlib bilan siqiladi, `.git/objects/` ichida saqlanadi va SHA-1 hash bilan
nomlanadi.

- **Blob**: faylning o'zi.
- **Tree**: papka ro'yxati. Har bir yozuv `<mode> <nom>\0<20 baytli sha>` ko'rinishida.
  Git papkalarni nomi `/` bilan tugagandek tartiblaydi. wyag ham shunday qiladi, aks holda
  tree hash'lari mos kelmaydi.
- **Commit va tag**: `kalit qiymat` qatorlari, bo'sh qator, keyin xabar. GPG imzosi kabi
  ko'p qatorli qiymatlar bo'sh joy bilan boshlanadigan qatorlarda davom etadi.
- **Ref'lar**: `HEAD` va branch'lar hash'ga yoki boshqa ref'ga ishora qiluvchi matn
  fayllari. `packed-refs` ham qo'llab-quvvatlanadi.

`log` commit grafini vaqt bo'yicha tartiblangan navbat (priority queue) bilan aylanib
chiqadi. Shu tufayli merge'li tarixni `git log` bilan bir xil tartibda chiqaradi.

## Tuzilishi

```
wyag/
├── __main__.py     # CLI: argumentlar va buyruqlar
├── repository.py   # GitRepository, init, repozitoriyni topish
├── objects.py      # Blob, commit, tree va tag; saqlash; nomlarni aniqlash
└── refs.py         # HEAD, branch, tag va packed-refs
tests/
└── test_wyag.py    # Unit, CLI va haqiqiy git bilan solishtirish testlari
```

## Testlar

```bash
pytest       # coverage bilan ishlaydi, 90% dan past bo'lsa xato beradi
ruff check .
```

CLI testlarining ko'pi haqiqiy `git` bilan repozitoriy yaratadi (ichki papkalar,
bajariladigan fayl, symlink, annotated tag, merge) va wyag natijasi bir xil ekanini
tekshiradi. CI hammasini Python 3.9, 3.11 va 3.13 versiyalarida ishga tushiradi.

## Cheklovlar

- **Packfile'lar hozircha qo'llab-quvvatlanmaydi.** `git gc` yoki `git clone`dan keyin
  obyektlarning ko'pi packfile'larda bo'ladi. Bunday holatda wyag tushunarli xato
  chiqaradi. Yangi repozitoriylar va yaqinda commit qilingan obyektlar bilan ishlaydi.
- Staging area (index) hali yo'q, shuning uchun wyag ishchi papkadan o'zi commit yarata
  olmaydi.
- Faqat SHA-1 repozitoriylar.

## Reja

- [x] `init`, `hash-object`, `cat-file`
- [x] Commit, tree va tag obyektlari; `log`, `ls-tree`, `checkout`
- [x] Ref'lar: `HEAD`, branch, tag, `packed-refs`
- [ ] Packfile o'qish (`.idx` v2, delta obyektlar)
- [ ] `show-ref`, `tag`, `rev-parse`
- [ ] Index: `ls-files`, `status`, `add`, `rm`, `commit`

## Litsenziya

[MIT](LICENSE)
