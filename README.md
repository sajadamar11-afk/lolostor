# حكايات الليل - صانع الفيديو

## صنع حلقة من الايفون
1. ارفع صور الحلقة لمجلد `episodes/اسم_الحلقة/images` (Add file ← Upload files).
   - سمّ الصور بأرقام المشاهد: `1.png` ، `2.png` ...
   - او ارفعها باسمائها (مثل IMG_5710.jpg) وتتوزع على المشاهد حسب ترتيب الارقام بالاسم.
2. افتح تبويب **Actions** ← **صنع فيديو الحلقة** ← **Run workflow**.
3. اكتب اسم الحلقة واضغط **Run workflow**.
4. بعد ما يخلص (علامة خضراء) افتح **Releases** بالصفحة الرئيسية وحمّل الفيديو.

## حلقة جديدة
سوّي مجلد جديد داخل `episodes` بيه `story.txt` ومجلد `images`.

## المفاتيح (مرة وحدة)
Settings ← Secrets and variables ← Actions ← New repository secret:
- `AZURE_KEY` = مفتاح Azure
- `AZURE_REGION` = northeurope
