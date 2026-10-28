# ---- config-------------------
PROJECT_ID="cs320-508117"
REGION="us-central1"
ZONE="us-central1-a"
BUCKET="skyvoong-cs528-hw2"
PREFIX="files/"

SQL_INSTANCE="hw5-sql"
SQL_TIER="db-f1-micro"
DB_NAME="hw5db"
DB_USER="hw5user"
DB_SECRET="hw5-db-pass"

TOPIC="hw5-forbidden-requests"
SUB="hw5-forbidden-requests-sub"

WEB_SA="hw5-web-sa@${PROJECT_ID}.iam.gserviceaccount.com"
FORBIDDEN_SA="hw5-forbidden-sa@${PROJECT_ID}.iam.gserviceaccount.com"
STOPPER_SA="hw5-sql-stopper-sa@${PROJECT_ID}.iam.gserviceaccount.com"

WEB_VM="hw5-web"
WEB_MACHINE="e2-small"
FORBIDDEN_VM="hw5-forbidden"
IP_NAME="hw5-web-ip"
FIREWALL="hw5-allow-http"
FUNCTION="hw5-sql-stopper"
JOB="hw5-sql-stopper-hourly"

HERE="$(cd "$(dirname "$0")" && pwd)"
VENV="$HOME/.hw5-venv"     # Python env for setup_schema.py / dbtool.py
G="gcloud --project=$PROJECT_ID --quiet"

exists() { "$@" >/dev/null 2>&1; }

# ---- APIs -------------------------------------------------------------------
enable_apis() {
    echo "== enabling APIs"
    $G services enable compute.googleapis.com sqladmin.googleapis.com pubsub.googleapis.com \
        logging.googleapis.com storage.googleapis.com secretmanager.googleapis.com \
        cloudfunctions.googleapis.com run.googleapis.com cloudbuild.googleapis.com \
        artifactregistry.googleapis.com cloudscheduler.googleapis.com iam.googleapis.com
}

# ---- service accounts -------------------------------------------------------
create_service_accounts() {
    echo "== service accounts"
    for pair in "hw5-web-sa:hw5 web server" \
                "hw5-forbidden-sa:hw5 forbidden-requests service" \
                "hw5-sql-stopper-sa:hw5 Cloud SQL stopper function"; do
        name="${pair%%:*}"; display="${pair#*:}"
        exists $G iam service-accounts describe "$name@${PROJECT_ID}.iam.gserviceaccount.com" \
            || $G iam service-accounts create "$name" --display-name="$display"
    done
}

# ---- database password (Secret Manager) ------------------------------------
create_db_secret() {
    echo "== database password secret"
    if ! exists $G secrets describe "$DB_SECRET"; then
        python3 -c 'import secrets; print(secrets.token_urlsafe(24), end="")' \
            | $G secrets create "$DB_SECRET" --replication-policy=automatic --data-file=-
    fi
    DB_PASS="$($G secrets versions access latest --secret="$DB_SECRET")"
}

# ---- Cloud SQL ----------------------------------------------------------------
# If the instance already exists it is just started. If the database (and its
# tables) do not exist yet, they are created and setup_schema.py builds the schema.
setup_cloud_sql() {
    echo "== Cloud SQL"
    if exists $G sql instances describe "$SQL_INSTANCE"; then
        echo "instance $SQL_INSTANCE exists: starting it"
        $G sql instances patch "$SQL_INSTANCE" --activation-policy=ALWAYS
    else
        echo "creating instance $SQL_INSTANCE (this takes several minutes)"
        $G sql instances create "$SQL_INSTANCE" --database-version=MYSQL_8_0 --edition=ENTERPRISE \
            --tier="$SQL_TIER" --region="$REGION" --storage-size=10 --no-backup \
            --activation-policy=ALWAYS
    fi

    if ! exists $G sql users describe "$DB_USER" --host=% --instance="$SQL_INSTANCE"; then
        $G sql users create "$DB_USER" --host=% --instance="$SQL_INSTANCE" --password="$DB_PASS"
    fi

    if exists $G sql databases describe "$DB_NAME" --instance="$SQL_INSTANCE"; then
        echo "database $DB_NAME exists: skipping schema creation"
    else
        $G sql databases create "$DB_NAME" --instance="$SQL_INSTANCE"
        echo "creating the schema with setup_schema.py"
        DB_PASS="$DB_PASS" "$VENV/bin/python" "$HERE/setup_schema.py"
    fi
}

# ---- local Python environment for setup_schema.py and dbtool.py --------------
# Kept outside the repo so it never gets committed.
setup_local_python() {
    echo "== local Python environment ($VENV)"
    [ -x "$VENV/bin/python" ] || python3 -m venv "$VENV"
    "$VENV/bin/pip" install -q "cloud-sql-python-connector[pymysql]" google-auth
}

# ---- Pub/Sub -------------------------------------------------------------------
setup_pubsub() {
    echo "== Pub/Sub"
    exists $G pubsub topics describe "$TOPIC" || $G pubsub topics create "$TOPIC"
    exists $G pubsub subscriptions describe "$SUB" \
        || $G pubsub subscriptions create "$SUB" --topic="$TOPIC" --ack-deadline=30
}

# ---- permissions (least privilege) -------------------------------------------
grant_permissions() {
    echo "== permissions"
    # web server: read the bucket, publish to one topic, write logs, connect to Cloud SQL,
    # read one secret
    $G storage buckets add-iam-policy-binding "gs://$BUCKET" \
        --member="serviceAccount:$WEB_SA" --role=roles/storage.objectViewer >/dev/null
    $G pubsub topics add-iam-policy-binding "$TOPIC" \
        --member="serviceAccount:$WEB_SA" --role=roles/pubsub.publisher >/dev/null
    for role in roles/logging.logWriter roles/cloudsql.client; do
        $G projects add-iam-policy-binding "$PROJECT_ID" \
            --member="serviceAccount:$WEB_SA" --role="$role" --condition=None >/dev/null
    done
    $G secrets add-iam-policy-binding "$DB_SECRET" \
        --member="serviceAccount:$WEB_SA" --role=roles/secretmanager.secretAccessor >/dev/null
    # forbidden service: consume one subscription
    $G pubsub subscriptions add-iam-policy-binding "$SUB" \
        --member="serviceAccount:$FORBIDDEN_SA" --role=roles/pubsub.subscriber >/dev/null
    # stopper function: read and stop Cloud SQL instances
    $G projects add-iam-policy-binding "$PROJECT_ID" \
        --member="serviceAccount:$STOPPER_SA" --role=roles/cloudsql.editor --condition=None >/dev/null
}

# ---- network: firewall + static IP -------------------------------------------
setup_network() {
    echo "== firewall and static IP"
    exists $G compute firewall-rules describe "$FIREWALL" \
        || $G compute firewall-rules create "$FIREWALL" --network=default --direction=INGRESS \
             --allow=tcp:80 --target-tags=hw5-web --source-ranges=0.0.0.0/0
    exists $G compute addresses describe "$IP_NAME" --region="$REGION" \
        || $G compute addresses create "$IP_NAME" --region="$REGION"
    WEB_IP="$($G compute addresses describe "$IP_NAME" --region="$REGION" --format='value(address)')"
}

# ---- VMs (configured only by startup.sh) -------------------------------------
create_vms() {
    echo "== web server VM ($WEB_MACHINE, static IP $WEB_IP)"
    exists $G compute instances describe "$WEB_VM" --zone="$ZONE" \
        || $G compute instances create "$WEB_VM" --zone="$ZONE" --machine-type="$WEB_MACHINE" \
             --image-family=debian-12 --image-project=debian-cloud \
             --address="$WEB_IP" --tags=hw5-web \
             --service-account="$WEB_SA" --scopes=cloud-platform \
             --metadata="role=web,project-id=$PROJECT_ID,region=$REGION,sql-instance=$SQL_INSTANCE,db-name=$DB_NAME,db-user=$DB_USER,db-secret=$DB_SECRET,bucket=$BUCKET,prefix=$PREFIX,topic=$TOPIC" \
             --metadata-from-file="startup-script=$HERE/startup.sh,app-py=$HERE/web_server.py"

    echo "== forbidden-requests VM"
    exists $G compute instances describe "$FORBIDDEN_VM" --zone="$ZONE" \
        || $G compute instances create "$FORBIDDEN_VM" --zone="$ZONE" --machine-type=e2-micro \
             --image-family=debian-12 --image-project=debian-cloud \
             --service-account="$FORBIDDEN_SA" --scopes=cloud-platform \
             --metadata="role=forbidden,project-id=$PROJECT_ID,subscription=$SUB" \
             --metadata-from-file="startup-script=$HERE/startup.sh,app-py=$HERE/forbidden_service.py"
}

# ---- Cloud Function + hourly scheduler ---------------------------------------
setup_stopper() {
    echo "== Cloud Function $FUNCTION"
    $G functions deploy "$FUNCTION" --gen2 --runtime=python312 --region="$REGION" \
        --source="$HERE/sql_stopper" --entry-point=stop_sql_if_running --trigger-http \
        --no-allow-unauthenticated --service-account="$STOPPER_SA" \
        --set-env-vars="PROJECT_ID=$PROJECT_ID,SQL_INSTANCE=$SQL_INSTANCE"
    URL="$($G functions describe "$FUNCTION" --gen2 --region="$REGION" --format='value(serviceConfig.uri)')"
    $G functions add-invoker-policy-binding "$FUNCTION" --region="$REGION" \
        --member="serviceAccount:$STOPPER_SA" >/dev/null

    echo "== Cloud Scheduler job $JOB (every hour)"
    if exists $G scheduler jobs describe "$JOB" --location="$REGION"; then
        $G scheduler jobs update http "$JOB" --location="$REGION" --schedule="0 * * * *" \
            --uri="$URL" --http-method=POST \
            --oidc-service-account-email="$STOPPER_SA" --oidc-token-audience="$URL"
    else
        $G scheduler jobs create http "$JOB" --location="$REGION" --schedule="0 * * * *" \
            --uri="$URL" --http-method=POST \
            --oidc-service-account-email="$STOPPER_SA" --oidc-token-audience="$URL"
    fi
}

enable_apis
create_service_accounts
create_db_secret
setup_local_python
setup_cloud_sql
setup_pubsub
grant_permissions
setup_network
create_vms
setup_stopper

cat <<EOF

Done. The VMs install and start their programs themselves; allow 3-5 minutes.
  Web server:  http://$WEB_IP/files/0.html
  Status:      gcloud compute ssh $WEB_VM --zone=$ZONE --project=$PROJECT_ID --command='sudo systemctl status hw5-web --no-pager'
  Tables:      $VENV/bin/python $HERE/dbtool.py show
Note: the hourly Cloud Function stops Cloud SQL. Before a long run, pause it:
  gcloud scheduler jobs pause $JOB --location=$REGION --project=$PROJECT_ID
EOF
