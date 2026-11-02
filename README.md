# wyag — Python'da Git

[![tests](https://github.com/octavvos/Building-Git/actions/workflows/tests.yml/badge.svg)](https://github.com/octavvos/Building-Git/actions/workflows/tests.yml)

Git qanday ishlashini ichidan tushunish uchun uni Python'da noldan yozyapman.
Loyiha [Write yourself a Git!](https://wyag.thb.lt/) qo'llanmasi asosida qurilgan.

## Hozir ishlaydigan buyruqlar

| Buyruq | Vazifasi |
|---|---|
| `init [papka]` | Yangi bo'sh repozitoriy yaratadi (`.git`, `HEAD`, `config`, `objects/`, `refs/`) |
| `hash-object [-w] fayl` | Fayl hash'ini hisoblaydi, `-w` bilan uni `.git/objects` ga yozadi |
| `cat-file hash` | Obyekt tarkibini chiqaradi (qisqa hash ham ishlaydi) |

Obyektlar haqiqiy Git formatida saqlanadi (`blob <hajm>\0<ma'lumot>`, zlib bilan siqilgan),
shuning uchun hash'lar asl `git` bilan bir xil chiqadi.

## Ishga tushirish

Python 3.9+ kerak, tashqi kutubxonalar yo'q.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

wyag init demo
cd demo
echo "hello world" > f.txt
wyag hash-object -w f.txt   # 3b18e512dba79e4c8300dd08aeb37f8e728b8dad
wyag cat-file 3b18e512      # hello world
```

## Testlar

```bash
pytest -v
```

Testlar wyag'ning o'zini tekshirish bilan birga haqiqiy `git` bilan mosligini ham tekshiradi:
wyag yozgan obyektni `git cat-file` o'qiy oladi, `git hash-object` yozganini esa wyag o'qiy oladi.
Har bir push'da testlar GitHub Actions'da Python 3.9, 3.11 va 3.13 versiyalarida ishga tushadi.

## Tuzilishi

```
wyag/
├── __main__.py     # CLI: buyruqlar va argumentlar
├── repository.py   # GitRepository, init, repozitoriyni topish
└── objects.py      # Obyektlarni o'qish, yozish, hash bo'yicha qidirish
tests/
└── test_wyag.py    # Unit, CLI va haqiqiy git bilan moslik testlari
```

## Reja

- [x] `init`, `hash-object`, `cat-file`
- [ ] `commit` va `tree` obyektlari
- [ ] `log`, `ls-tree`, `checkout`
- [ ] `show-ref`, `tag`, `rev-parse`
- [ ] `ls-files`, `status`, `add`, `commit`
