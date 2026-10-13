set -u
K="${1:?clients 1-4}"; IP="${2:?web server ip}"; MAXI="${3:?max file index}"
SECS="${4:-180}"; CT="${5:-10}"
[ "$K" -ge 1 ] && [ "$K" -le 4 ] || { echo "K must be between 1 and 4"; exit 1; }

# $CLIENT is either the provided Linux binary or: "python3 http-client.pyc"
CLIENT="${CLIENT:-python3 http-client.pyc}"
mkdir -p out
rm -f out/client_${K}_*.txt
for i in $(seq 1 "$K"); do
  timeout "$SECS" $CLIENT -d "$IP" -b none -w "${WEBDIR:-files}" -i "$MAXI" -n 10000000 \
      -t "$CT" -r "$((100 + i))" > "out/client_${K}_$i.txt" 2>&1 &
done
wait
echo "== results for $K concurrent client(s), $SECS s"
for i in $(seq 1 "$K"); do
  echo "client $i: timeouts=$(grep -c 'Timeout received' out/client_${K}_$i.txt)" \
       "errors=$(grep -cE 'Error|Traceback' out/client_${K}_$i.txt)"
  grep -E 'Error' out/client_${K}_$i.txt | sort | uniq -c | head -3
done