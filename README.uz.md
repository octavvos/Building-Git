# wyag — Python'da Git

[![tests](https://github.com/octavvos/Building-Git/actions/workflows/tests.yml/badge.svg)](https://github.com/octavvos/Building-Git/actions/workflows/tests.yml)
![coverage](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/octavvos/Building-Git/badges/coverage.json)
![python](https://img.shields.io/badge/python-3.9%2B-blue)
[![license](https://img.shields.io/badge/license-MIT-lightgrey)](LICENSE)

[English](README.md) | **O'zbekcha**

Git sof Python'da noldan yozilgan. Maqsad: Git tarixni qanday saqlashi, o'zgarishlarni
qanday kuzatishi va branch'larni qanday birlashtirishini ichidan tushunish. Loyiha
[Write yourself a Git!](https://wyag.thb.lt/) qo'llanmasidan boshlangan va undan ancha
uzoqqa ketgan: packfile'lar, binar index, `.gitignore`, branch'lar, diff va konflikt
markerlari bilan haqiqiy uch tomonlama merge.

wyag Git bilan bir xil disk formatida ishlaydi, shuning uchun haqiqiy repozitoriylarda,
jumladan yangi klon qilinganlarida ham ishlaydi, va istalgan paytda `git` bilan `wyag`
orasida almashish mumkin. Testlar har bir buyruqni haqiqiy `git` bilan yonma-yon ishlatib,
natijalarni solishtiradi. Commit, tag va merge commit'lar git yaratadiganlari bilan
**bir xil hash**ga ega. `status`, `diff`, `log`, `ls-files` va konfliktli fayllar
bayt-baytigacha solishtiriladi.

## Buyruqlar

**Kundalik ish**

| Buyruq | Vazifasi |
|---|---|
| `init [papka]` | Bo'sh repozitoriy yaratadi |
| `status [-s]` | Staged, unstaged, kuzatilmagan va konfliktli fayllar; `-s` = `git status --porcelain` |
| `add [-f] yo'l...` | Fayl yoki papkalarni index'ga qo'shadi (`.gitignore`ni hisobga oladi) |
| `rm [--cached] [-r] yo'l...` | Fayllarni o'chiradi; saqlanmagan o'zgarishlarni o'chirmaydi |
| `commit [-a] [-m xabar]` | Index'ni commit qiladi; `-m`siz git kabi muharrir ochadi |
| `diff [--cached] [rev [rev]]` | Fayllar↔index, index↔`HEAD` yoki ikki commit orasidagi farq |
| `log [-n N] [--oneline] [rev]` | Tarix, merge'lar bilan |

**Branch'lar va merge**

| Buyruq | Vazifasi |
|---|---|
| `branch [-d \| -D] [nom [boshlanish]]` | Branch'lar ro'yxati, yaratish, o'chirish (`-d` faqat merge qilingan bo'lsa) |
| `switch [-c] [--detach] maqsad` | Branch'ga o'tish, fayllar va index xavfsiz yangilanadi |
| `merge [--no-ff] [-m xabar] rev` | Fast-forward yoki uch tomonlama merge, konflikt markerlari bilan |
| `merge --abort` | Konfliktli merge'ni bekor qilish |
| `merge-base [--all] a b` | Eng yaxshi umumiy ajdod(lar) |
| `tag [-a] [-m xabar] [nom [rev]]` | Tag'lar ro'yxati yoki yangi tag |

**Ichki (plumbing) buyruqlar**

| Buyruq | Vazifasi |
|---|---|
| `cat-file [-t \| -p] rev` | Obyekt tarkibi yoki turi |
| `hash-object [-w] fayl` | Fayl hash'i, `-w` bilan saqlaydi |
| `ls-tree [-r] rev` | Tree tarkibi |
| `ls-files [-s]` | Index tarkibi, konflikt paytida bosqichlar bilan |
| `rev-parse [--short] rev...` | Revision'larni hash'ga aylantiradi |
| `show-ref [--heads] [--tags]` | Ref'lar ro'yxati |
| `checkout rev papka` | Commit fayllarini bo'sh papkaga eksport qiladi |

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
wyag add . && wyag commit -m "Birinchi commit"

wyag switch -c feature
echo "feature'dan" >> salom.txt
wyag commit -a -m "Feature ishi"

wyag switch master
wyag merge feature
wyag log --oneline
```

Istalgan mavjud git repozitoriyda ham ishlaydi: o'z loyihangizda
`wyag log --oneline -n 5`, `wyag status` yoki `wyag diff HEAD~1 HEAD`ni sinab ko'ring.

## Qanday ishlaydi

**Obyektlar.** Har bir obyekt `<tur> <hajm>\0<tarkib>` ko'rinishida zlib bilan siqiladi
va SHA-1 bilan nomlanadi. Blob — fayl tarkibi. Tree — papka ro'yxati,
`<mode> <nom>\0<20 baytli sha>` yozuvlari (papka nomlari `/` bilan tugagandek
tartiblanadi, aks holda hash noto'g'ri chiqadi). Commit va tag — `kalit qiymat`
qatorlari, bo'sh qator va xabar.

**Packfile'lar.** `git gc` va `git clone` obyektlarni bitta faylga yig'adi, ko'pini
*delta* ko'rinishida: boshqa obyektga nisbatan "nusxa ol / qo'sh" ko'rsatmalari. wyag
`.idx`ni o'qiydi (2 GiB'dan katta pack'lar uchun 64 bitli offset jadvali bilan),
`OFS_DELTA` / `REF_DELTA` zanjirini to'liq obyektgacha kuzatib, ko'rsatmalarni bajaradi.

**Index.** `.git/index` — har bir staged fayl, uning hash'i, mode'i va `stat()`
ma'lumotlari yozilgan binar fayl (2, 3 va yo'llari siqilgan 4-versiya). `status`
o'zgarmagan fayllarni qayta hash qilmaslik uchun `stat()`dan foydalanadi, lekin index
yozilgan soniyada o'zgargan fayllarni baribir tekshiradi ("racy git"). Index git kabi
`index.lock` orqali atomik yoziladi.

**Branch almashtirish.** Ikki commit orasida o'tish — ikki tomonlama merge: faqat farq
qiladigan fayllarga tegiladi, boshqa fayllardagi lokal o'zgarishlar saqlanib qoladi,
va o'tish saqlanmagan ish yoki kuzatilmagan fayl ustidan yozadigan bo'lsa, hech narsaga
tegmasdan oldindan rad etiladi.

**Diff.** Hunk'lar Python'ning `difflib` moduli bilan hisoblanadi va git sarlavhalari,
`\ No newline at end of file`, binar fayllarni aniqlash va git'ning "funksiya nomi"
konteksti bilan chiqariladi. Git Myers algoritmini qo'shimcha evristikalar bilan
ishlatadi, shuning uchun katta o'zgarishlarda hunk'lar boshqacha bo'linishi mumkin. Lekin
har bir patch `git apply` bilan qo'llanadi va aynan kerakli natijani beradi.

**Merge.** Merge asosi commit grafini aylanib topiladi. Keyin har bir fayl uch tomonlama
birlashtiriladi: faqat bir tomon o'zgartirgan bo'lsa, o'sha tomon olinadi; ikkala tomon
matnli faylni o'zgartirgan bo'lsa, *diff3* algoritmi qatorlarni birlashtiradi, ikkala
tomon kelishgan qatorlarni konfliktdan chiqaradi, haqiqiy kelishmovchilik joylariga esa
`<<<<<<<` / `=======` / `>>>>>>>` markerlarini yozadi. Konfliktlar index'ga 1–3
bosqichlar (asos, bizniki, ularniki) sifatida, `MERGE_HEAD` va `MERGE_MSG` bilan yoziladi.
Shuning uchun `status`, `commit` va hatto haqiqiy `git` ham ishni wyag to'xtagan joydan
davom ettira oladi.

## Tuzilishi

```
wyag/
├── __main__.py     # CLI: argumentlar va natijani chiqarish
├── repository.py   # GitRepository, init, repozitoriyni topish
├── objects.py      # Blob, commit, tree, tag; o'qish/yozish; revision sintaksisi
├── pack.py         # Packfile va .idx o'qish, delta'larni ochish
├── refs.py         # HEAD, branch, tag, packed-refs, ref nomi qoidalari
├── index.py        # .git/index o'qish va yozish (v2, v3, v4)
├── ignore.py       # .gitignore, info/exclude va core.excludesFile qoidalari
├── worktree.py     # status, add, rm, commit, fayllarni tree'dan yangilash
├── branch.py       # branch va switch
├── diff.py         # Unified diff
├── merge.py        # diff3, tree merge, merge/abort
├── history.py      # Ajdodlar va merge asosi
└── config.py       # git config va muallif ma'lumoti
tests/              # ~260 ta test, ko'pi haqiqiy git bilan solishtiradi
```

## Testlar

```bash
pytest       # coverage bilan ishlaydi, 90% dan past bo'lsa xato beradi
ruff check .
```

Testlar haqiqiy `git` bilan repozitoriylar yaratadi (merge'lar, criss-cross tarix,
annotated tag'lar, symlink, bajariladigan fayllar, chuqur delta zanjirlari, merge
konfliktlari, submodule yozuvlari) va wyag natijalari hamda hash'larini git'niki bilan
solishtiradi. `.gitignore` qoidalari `git check-ignore` bilan tekshiriladi. CI hammasini
Python 3.9, 3.11 va 3.13 versiyalarida ishga tushiradi.

## Cheklovlar

- Tarmoq (`clone`, `fetch`, `push`), `rebase`, `stash`, `reset` va reflog yo'q.
- Merge birinchi eng yaxshi asosdan foydalanadi. Git'ning criss-cross tarix uchun
  "recursive" usuli (bir nechta asosni virtual asosga birlashtirish) va qayta nomlashni
  aniqlash yo'q. Bir tomonda fayl papkaga aylangan holat qo'llab-quvvatlanmaydi.
- Katta o'zgarishlarda `diff` hunk'lari git'nikidan farq qilishi mumkin (yuqoriga
  qarang). Qayta nomlashni aniqlash va `--stat` yo'q.
- Faqat SHA-1 repozitoriylar.

## Litsenziya

[MIT](LICENSE)
