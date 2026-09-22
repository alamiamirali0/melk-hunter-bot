#!/usr/bin/env bash
# ============================================================
#  لانچر خودترمیم ربات ملک هانتر
#  اگر ربات به هر دلیلی بمیرد (خطا، مرگ حلقهٔ polling، قطع شبکه)
#  بلافاصله نسخهٔ تازه بالا می‌آید. فقط با SIGTERM/SIGINT متوقف می‌شود.
# ============================================================
set -u
cd "$(dirname "$0")"

HARD_LIMIT="${1:-20700}"     # سقف کل اجرا (ثانیه) — پیش‌فرض ۵ ساعت و ۴۵ دقیقه
START=$(date +%s)
RESTARTS=0

echo "🚀 لانچر خودترمیم شروع شد (سقف ${HARD_LIMIT}s)"
mkdir -p logs
rm -f logs/healthy.marker   # نشانهٔ سلامت اجرای قبلی پاک شود

# نشانهٔ سلامت: فقط اگر ربات بیش از ۱۰ دقیقه واقعاً سالم کار کند نوشته می‌شود.
# زنجیره‌سازی به این نشانه نگاه می‌کند تا اجرای خراب، زنجیرهٔ خراب نسازد.
mark_healthy() {
  while true; do
    sleep 20
    NOW=$(date +%s)
    if [ $((NOW - START)) -gt 600 ]; then
      date -u +"%Y-%m-%dT%H:%M:%SZ" > logs/healthy.marker
    fi
  done
}
mark_healthy &
MARKER_PID=$!

while true; do
  NOW=$(date +%s)
  ELAPSED=$((NOW - START))
  LEFT=$((HARD_LIMIT - ELAPSED))
  if [ "$LEFT" -le 30 ]; then
    echo "⏰ سقف اجرا تمام شد (${ELAPSED}s، ${RESTARTS} ری‌استارت) — پایان مرتب"
    break
  fi

  echo "── اجرای ربات (تلاش $((RESTARTS + 1)) | ${ELAPSED}s از ${HARD_LIMIT}s) ──"
  # timeout داخلی: اگر پروسه قفل کرد، ۶۰ ثانیه قبل از سقف با INT ببندیم
  timeout --signal=INT $((LEFT - 20)) python -u -m melkbot.bot
  CODE=$?
  if [ "$CODE" -eq 124 ] || [ "$CODE" -eq 0 ]; then
    echo "✅ ربات مرتب تمام شد (کد $CODE)"
    break
  fi
  RESTARTS=$((RESTARTS + 1))
  if [ ! -f logs/healthy.marker ] && [ "$RESTARTS" -ge 25 ]; then
    echo "💥 ۲۵ بار پیاپی ربات بالا نیامد — پایان اجرا تا اجرای تازه با کد سالم بیاید"
    exit 9
  fi
  echo "♻️ ربات با کد $CODE متوقف شد — ۳ ثانیه بعد نسخهٔ تازه بالا می‌آید"
  sleep 3
done

kill $MARKER_PID 2>/dev/null || true
echo "🏁 لانچر تمام شد | تعداد ری‌استارت: ${RESTARTS}"
