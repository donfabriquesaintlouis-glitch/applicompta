import streamlit as st
import pandas as pd
import json
import os
from datetime import datetime
import hashlib
from firebase_admin import credentials, initialize_app, firestore
# Dans app.py:
firebase_config_str = os.environ.get('__firebase_config')
# ...
firebase_config = json.loads(firebase_config_str)
cred = credentials.Certificate(firebase_config)
# --- Configuration ---
APP_ID = os.environ.get('__app_id', 'compta-smmd-default')
USER_ID = os.environ.get('__user_id', 'unknown_user') 

# Chemins Firestore
COL_USERS = f"artifacts/{APP_ID}/public/data/smmd_users"
COL_HOUSES = f"artifacts/{APP_ID}/public/data/smmd_houses"
COL_TRANSACTIONS = f"artifacts/{APP_ID}/public/data/smmd_transactions"
COL_ALLOCATIONS = f"artifacts/{APP_ID}/public/data/smmd_allocations"

# Constantes
ROLES = ["admin", "chef_de_maison", "normal"]
TITLES = ["Abbé", "Frère"]
PAYMENT_METHODS = ["CB Maison", "CB Personnelle (Avance)", "Chèque Personnel (Avance)", "Liquide Personnel (Avance)"]
HOUSE_PAYMENT_METHODS = ["CB Maison"]

# --- Initialisation Firebase ---
@st.cache_resource
def initialize_firebase():
    try:
        firebase_config_str = os.environ.get('__firebase_config')
        if not firebase_config_str:
            st.error("Erreur: Config Firebase introuvable.")
            return None
        
        firebase_config = json.loads(firebase_config_str)
        cred = credentials.Certificate(firebase_config)
        
        try:
            app = initialize_app(cred, name=APP_ID)
        except ValueError:
            import firebase_admin
            app = firebase_admin.get_app(name=APP_ID)
            
        return firestore.client(app=app)
    except Exception as e:
        st.error(f"Erreur init: {e}")
        return None

db = initialize_firebase()
if db is None:
    st.stop()

# --- Authentification ---
def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

@st.cache_data
def get_all_users(refresh=False):
    try:
        docs = db.collection(COL_USERS).stream()
        return {doc.id: doc.to_dict() for doc in docs}
    except: return {}

def authenticate_user(username, password):
    try:
        q = db.collection(COL_USERS).where('username', '==', username).limit(1).stream()
        user_doc = next(q, None)
        if user_doc:
            user_data = user_doc.to_dict()
            if user_data.get('password_hash') == hash_password(password):
                st.session_state['logged_in'] = True
                st.session_state['user_data'] = user_data
                st.session_state['user_id'] = user_doc.id 
                st.session_state['role'] = user_data.get('role')
                st.session_state['house_id'] = user_data.get('house_id')
                return True
        return False
    except: return False

def logout():
    st.session_state['logged_in'] = False
    st.session_state['user_data'] = {}
    st.session_state['role'] = None
    st.rerun()

# --- Transactions & Calculs ---
def save_transaction(house_id, user_id, type, amount, nature, payment_method=None, notes=None):
    try:
        data = {
            'house_id': house_id, 'user_id': user_id, 'type': type,
            'amount': round(float(amount), 2), 'nature': nature,
            'payment_method': payment_method, 'created_at': datetime.now().isoformat(),
            'status': 'validé' if type != 'depense_avance' else 'en_attente_remboursement', 
            'month_year': datetime.now().strftime('%Y-%m') 
        }
        db.collection(COL_TRANSACTIONS).add(data)
        st.toast("Enregistré !", icon='✅')
        get_house_transactions.clear()
        return True
    except Exception as e:
        st.error(f"Erreur: {e}")
        return False

@st.cache_data(ttl=60)
def get_house_transactions(house_id):
    try:
        query = db.collection(COL_TRANSACTIONS).where('house_id', '==', house_id).stream()
        return pd.DataFrame([d.to_dict() | {'doc_id': d.id} for d in query])
    except: return pd.DataFrame()

@st.cache_data
def get_all_houses():
    try:
        docs = db.collection(COL_HOUSES).stream()
        return {doc.id: doc.to_dict() for doc in docs}
    except: return {}

@st.cache_data
def get_house_name(house_id):
    try:
        doc = db.collection(COL_HOUSES).document(house_id).get()
        return doc.to_dict().get('name', 'Inconnue') if doc.exists else 'Inconnue'
    except: return 'Inconnue'

def calculate_balances(df, uid):
    recettes = df[df['type'].str.contains('recette')]['amount'].sum()
    depenses_maison = df[df['payment_method'] == 'CB Maison']['amount'].sum()
    house_bal = round(recettes - depenses_maison, 2)
    
    avances = df[(df['user_id'] == uid) & (df['type'] == 'depense_avance')]['amount'].sum()
    remb = df[(df['user_id'] == uid) & (df['type'] == 'depense_avance') & (df['status'] == 'remboursé')]['amount'].sum()
    perso_bal = round(avances - remb, 2)
    return house_bal, perso_bal

def set_monthly_allocation(user_id, house_id, amount):
    amount = round(float(amount), 2)
    db.collection(COL_ALLOCATIONS).document(user_id).set({'amount': amount, 'updated': datetime.now().isoformat()})
    
    current_month = datetime.now().strftime('%Y-%m')
    q = db.collection(COL_TRANSACTIONS).where('user_id', '==', user_id).where('month_year', '==', current_month).where('type', '==', 'recette_mensuelle').limit(1).stream()
    ex = next(q, None)
    
    u_name = st.session_state['user_data'].get('first_name', 'User')
    if ex:
        db.collection(COL_TRANSACTIONS).document(ex.id).update({'amount': amount})
    else:
        save_transaction(house_id, user_id, 'recette_mensuelle', amount, f"Alloc {u_name}")
    st.rerun()

def delete_transaction(doc_id):
    try:
        db.collection(COL_TRANSACTIONS).document(doc_id).delete()
        st.toast("Supprimé !", icon='🗑️')
        get_house_transactions.clear()
        st.rerun()
    except Exception as e: st.error(str(e))


# --- Interfaces ---
def admin_interface():
    st.header("👑 Admin")
    t1, t2, t3 = st.tabs(["Utilisateurs", "Maisons", "Audit"])
    
    with t1:
        with st.form("new_user"):
            c1, c2, c3 = st.columns(3)
            ti = c1.selectbox("Titre", TITLES)
            fn = c2.text_input("Prénom")
            ln = c3.text_input("Nom")
            pw = st.text_input("Mdp", type="password")
            houses = get_all_houses()
            h_opts = {v['name']: k for k, v in houses.items()}
            role = st.selectbox("Rôle", ROLES)
            house = st.selectbox("Maison", list(h_opts.keys()) if h_opts else ["-"])
            
            if st.form_submit_button("Créer"):
                uname = f"{fn.lower()}_{ln.lower()}"
                db.collection(COL_USERS).document(uname).set({
                    'title': ti, 'first_name': fn, 'last_name': ln, 'username': uname,
                    'password_hash': hash_password(pw), 'role': role, 'house_id': h_opts[house]
                })
                get_all_users.clear()
                st.success(f"Créé: {uname}")

    with t2:
        with st.form("new_house"):
            name = st.text_input("Nom Ville")
            if st.form_submit_button("Créer"):
                hid = name.lower().replace(' ', '_')
                db.collection(COL_HOUSES).document(hid).set({'name': name})
                get_all_houses.clear()
                st.rerun()

    with t3:
        all_tx = [d.to_dict() | {'id': d.id} for d in db.collection(COL_TRANSACTIONS).stream()]
        if all_tx: st.dataframe(pd.DataFrame(all_tx))

def user_dashboard():
    hid = st.session_state['house_id']
    role = st.session_state['role']
    df = get_house_transactions(hid)
    h_bal, p_bal = calculate_balances(df, st.session_state['user_id']) if not df.empty else (0,0)
    
    st.title(f"🏠 {get_house_name(hid)}")
    c1, c2 = st.columns(2)
    c1.metric("Solde Maison", f"{h_bal} €")
    c2.metric("Vos Avances", f"{p_bal} €")
    
    tabs = ["Recettes", "Dépenses"]
    if role == 'chef_de_maison': tabs.append("Chef")
    
    t_list = st.tabs(tabs)
    
    with t_list[0]: # Recettes
        with st.form("alloc"):
            v = st.number_input("Allocation", min_value=0.0)
            if st.form_submit_button("Valider"): set_monthly_allocation(st.session_state['user_id'], hid, v)
        with st.form("rec"):
            v = st.number_input("Montant", min_value=0.0)
            n = st.text_input("Nature")
            if st.form_submit_button("Ajouter"): 
                save_transaction(hid, st.session_state['user_id'], 'recette_exceptionnelle', v, n)
                st.rerun()

    with t_list[1]: # Dépenses
        with st.form("dep"):
            v = st.number_input("Montant", min_value=0.0)
            n = st.text_input("Nature")
            m = st.radio("Moyen", PAYMENT_METHODS)
            if st.form_submit_button("Ajouter"):
                typ = 'depense_maison' if m in HOUSE_PAYMENT_METHODS else 'depense_avance'
                save_transaction(hid, st.session_state['user_id'], typ, v, n, m)
                st.rerun()

    if role == 'chef_de_maison' and len(t_list) > 2:
        with t_list[2]: # Chef
            if not df.empty:
                st.dataframe(df)
                pending = df[(df['type'] == 'depense_avance') & (df['status'] == 'en_attente_remboursement')]
                if not pending.empty:
                    st.warning(f"{len(pending)} validations en attente")
                    uids = pending['user_id'].unique()
                    u = st.selectbox("Membre", uids)
                    if st.button("Valider Remboursement"):
                        for d in db.collection(COL_TRANSACTIONS).where('user_id','==',u).where('status','==','en_attente_remboursement').stream():
                            db.collection(COL_TRANSACTIONS).document(d.id).update({'status': 'remboursé'})
                        st.success("Validé")
                        get_house_transactions.clear()
                        st.rerun()

# --- Main Loop ---
if __name__ == '__main__':
    st.set_page_config(page_title="Compta Smmd", page_icon="💰")
    if 'logged_in' not in st.session_state: st.session_state['logged_in'] = False
    
    if st.session_state['logged_in']:
        if st.sidebar.button("Déconnexion"): logout()
        if st.session_state['role'] == 'admin': admin_interface()
        else: user_dashboard()
    else:
        st.title("Connexion")
        u = st.text_input("User (prenom_nom)")
        p = st.text_input("Password", type="password")
        if st.button("Se connecter"):
            if authenticate_user(u, p): st.rerun()
            else: st.error("Erreur")



