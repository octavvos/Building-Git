# wyag — Python'da Git

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

Python 3.8+ kerak, tashqi kutubxonalar yo'q.

```bash
python3 -m wyag init demo
cd demo
echo "hello world" > f.txt
python3 -m wyag hash-object -w f.txt   # 3b18e512dba79e4c8300dd08aeb37f8e728b8dad
python3 -m wyag cat-file 3b18e512      # hello world
```

`demo` papkasidan ishlatganda loyiha papkasini `PYTHONPATH` ga qo'shing:

```bash
export PYTHONPATH=/path/to/Building-Git
```

## Tuzilishi

```
wyag/
├── __main__.py     # CLI: buyruqlar va argumentlar
├── repository.py   # GitRepository, init, repozitoriyni topish
└── objects.py      # Obyektlarni o'qish, yozish, hash bo'yicha qidirish
```

## Reja

- [x] `init`, `hash-object`, `cat-file`
- [ ] `commit` va `tree` obyektlari
- [ ] `log`, `ls-tree`, `checkout`
- [ ] `show-ref`, `tag`, `rev-parse`
- [ ] `ls-files`, `status`, `add`, `commit`
