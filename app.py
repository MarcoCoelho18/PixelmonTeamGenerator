from flask import Flask, render_template, request, session
import requests
import random
import pickle
import os
import atexit
from concurrent.futures import ThreadPoolExecutor
from apscheduler.schedulers.background import BackgroundScheduler

app = Flask(__name__)
app.secret_key = 'dev-secret-key'

CACHE_FILE = 'pokemon_cache.pkl'
pokemon_cache = {}
evolution_families = {}

GENERATION_RANGES = {
    '1': (1, 151),
    '2': (152, 251),
    '3': (252, 386),
    '4': (387, 493),
    '5': (494, 649),
    '6': (650, 721),
    '7': (722, 809),
    '8': (810, 905),
    '9': (906, 1025)
}

def validate_cache(cache_data):
    valid_cache = {}
    for pokemon_id, data in cache_data.items():
        if data and isinstance(data, dict) and 'id' in data:
            valid_cache[pokemon_id] = data
    return valid_cache

def load_cache():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, 'rb') as f:
                data = pickle.load(f)
                return validate_cache(data.get('pokemon', {})), data.get('evolution', {})
        except (pickle.PickleError, EOFError):
            print("Cache file corrupted, starting fresh")
            return {}, {}
    return {}, {}

def save_cache():
    with open(CACHE_FILE, 'wb') as f:
        pickle.dump({'pokemon': pokemon_cache, 'evolution': evolution_families}, f)

def pre_cache_pokemon():
    """Pre-load all Pokémon data into cache"""
    print("Pre-caching Pokémon data...")
    with ThreadPoolExecutor(max_workers=10) as executor:
        executor.map(get_pokemon_data, range(1, 1026))
    print("Pre-caching complete!")

def update_cache():
    """Update the Pokémon cache"""
    print("Starting cache update...")
    with ThreadPoolExecutor(max_workers=10) as executor:
        executor.map(get_pokemon_data, range(1, 1026))
    print("Cache update complete!")

pokemon_cache, evolution_families = load_cache()
atexit.register(save_cache)

def clean_pixelmon_name(name):
    return name.replace(' ', '_').replace('-', '_')

def get_evolution_family(species_url):
    if species_url in evolution_families:
        return evolution_families[species_url]
    
    try:
        response = requests.get(species_url, timeout=10)
        response.raise_for_status()
        species_data = response.json()
        
        if not species_data.get('evolution_chain'):
            evolution_families[species_url] = [species_data['id']]
            return [species_data['id']]
        
        evolution_response = requests.get(species_data['evolution_chain']['url'], timeout=10)
        evolution_response.raise_for_status()
        evolution_data = evolution_response.json()
        
        family_ids = []
        chain = evolution_data['chain']
        
        def get_chain_ids(chain):
            species_id = int(chain['species']['url'].split('/')[-2])
            family_ids.append(species_id)
            for evolution in chain['evolves_to']:
                get_chain_ids(evolution)
        
        get_chain_ids(chain)
        evolution_families[species_url] = family_ids
        return family_ids
    
    except Exception as e:
        print(f"Error fetching evolution chain: {e}")
        return []

def get_pokemon_data(pokemon_id):
    if pokemon_id in pokemon_cache:
        cached = pokemon_cache[pokemon_id]
        if cached is not None:
            return cached
    
    try:
        response = requests.get(f"https://pokeapi.co/api/v2/pokemon/{pokemon_id}", timeout=10)
        response.raise_for_status()
        data = response.json()
        
        species_response = requests.get(data['species']['url'], timeout=10)
        species_response.raise_for_status()
        species_data = species_response.json()
        
        species_name = next(
            (name['name'] for name in species_data['names'] 
             if name['language']['name'] == 'en'),
            species_data['name']
        )
        
        evolution_family = get_evolution_family(data['species']['url'])
        
        first_evolution = None
        if species_data.get('evolution_chain'):
            try:
                evolution_response = requests.get(species_data['evolution_chain']['url'], timeout=10)
                evolution_response.raise_for_status()
                evolution_data = evolution_response.json()
                chain = evolution_data['chain']
                first_evo_id = int(chain['species']['url'].split('/')[-2])
                if first_evo_id != pokemon_id:
                    first_evolution = get_pokemon_data(first_evo_id)
            except Exception as e:
                print(f"Error fetching evolution chain for {pokemon_id}: {e}")
        
        pixelmon_name = clean_pixelmon_name(species_name)
        pixelmon_url = f"https://pixelmonmod.com/wiki/{pixelmon_name}"
        
        pokemon = {
            'id': data['id'],
            'name': species_name,
            'sprite': data['sprites'].get('front_default', ''),
            'types': [t['type']['name'].capitalize() for t in data['types']],
            'is_legendary': species_data.get('is_legendary', False) or species_data.get('is_mythical', False),
            'first_evolution': first_evolution,
            'wiki_url': pixelmon_url,
            'evolution_family': evolution_family
        }
        
        pokemon_cache[pokemon_id] = pokemon
        return pokemon
    
    except requests.exceptions.RequestException as e:
        print(f"Error fetching Pokémon {pokemon_id}: {e}")
        pokemon_cache[pokemon_id] = None
        return None
    except KeyError as e:
        print(f"Unexpected data structure for Pokémon {pokemon_id}: {e}")
        pokemon_cache[pokemon_id] = None
        return None

def get_random_team(legendary_count, generation=None):
    team = []
    used_families = set()
    max_attempts = 100
    
    if generation and generation in GENERATION_RANGES:
        min_id, max_id = GENERATION_RANGES[generation]
        available_pokemon = [p for p in pokemon_cache.values() 
                           if p and min_id <= p['id'] <= max_id]
    else:
        available_pokemon = [p for p in pokemon_cache.values() if p]

    if not available_pokemon:
        return []
    
    legendaries = [p for p in available_pokemon if p and p.get('is_legendary')]
    regulars = [p for p in available_pokemon if p and not p.get('is_legendary')]
    
    attempts = 0
    while len(team) < 6 and attempts < max_attempts:
        attempts += 1
        
        if len(team) < legendary_count and legendaries:
            pokemon = random.choice(legendaries)
            if pokemon and not any(fam in used_families for fam in pokemon.get('evolution_family', [])):
                team.append(pokemon)
                used_families.update(pokemon.get('evolution_family', []))
                continue
        
        if regulars:
            pokemon = random.choice(regulars)
            if pokemon and not any(fam in used_families for fam in pokemon.get('evolution_family', [])):
                team.append(pokemon)
                used_families.update(pokemon.get('evolution_family', []))
    
    return team[:6]

@app.route('/clear_team', methods=['POST'])
def clear_team():
    if 'team' in session:
        session.pop('team')
    return '', 204

@app.route('/', methods=['GET', 'POST'])
def index():
    # Clear team on page refresh
    if request.method == 'GET' and 'team' in session:
        session.pop('team')
    
    # Initialize default form values
    form_values = {
        'legendary_count': 0,
        'generation': None
    }
    
    # Handle form submission for new team
    if request.method == 'POST':
        try:
            form_values['legendary_count'] = min(max(int(request.form.get('legendary_count', 0)), 0), 6)
            form_values['generation'] = request.form.get('generation', None)
            if form_values['generation'] and form_values['generation'] not in GENERATION_RANGES:
                form_values['generation'] = None
                
            new_team = get_random_team(
                form_values['legendary_count'],
                form_values['generation']
            )
            if new_team:
                session['team'] = new_team
                session['form_values'] = form_values
            
        except (ValueError, TypeError) as e:
            print(f"Error processing form: {e}")
    
    # Use saved form values if available
    if 'form_values' in session:
        form_values = session['form_values']
    
    return render_template('index.html', 
                         team=session.get('team'),
                         legendary_count=form_values['legendary_count'],
                         generation=form_values['generation'])

if __name__ == '__main__':
    if not pokemon_cache:
        pre_cache_pokemon()
    
    scheduler = BackgroundScheduler()
    scheduler.add_job(update_cache, 'cron', hour=3)
    scheduler.start()
    
    app.run(debug=True)