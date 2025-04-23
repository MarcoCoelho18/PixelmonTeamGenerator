from flask import Flask, render_template, request, session, jsonify
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
def clean_pokemon_name(name):
    # Replace different types of hyphens/dashes with a standard hyphen
    name = name.replace('‑', '-').replace('–', '-').replace('—', '-')
    # Handle spaces and apostrophes
    name = name.replace(' ', '_').replace("'", "%27")
    return name
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
    print("Pre-caching Pokémon data...")
    with ThreadPoolExecutor(max_workers=10) as executor:
        executor.map(get_pokemon_data, range(1, 1026))
    print("Pre-caching complete!")

def update_cache():
    print("Starting cache update...")
    with ThreadPoolExecutor(max_workers=10) as executor:
        executor.map(get_pokemon_data, range(1, 1026))
    print("Cache update complete!")

pokemon_cache, evolution_families = load_cache()
atexit.register(save_cache)


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
        
        pokemon_name = clean_pokemon_name(species_name)
        bulbapedia_url = f"https://bulbapedia.bulbagarden.net/wiki/{pokemon_name}_(Pokémon)"
        
        pokemon = {
            'id': data['id'],
            'name': species_name,
            'sprite': data['sprites'].get('front_default', ''),
            'types': [t['type']['name'].capitalize() for t in data['types']],
            'is_legendary': species_data.get('is_legendary', False) or species_data.get('is_mythical', False),
            'first_evolution': first_evolution,
            'wiki_url': bulbapedia_url,
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

def get_random_pokemon(existing_team, legendary_count, generation=None):
    used_families = {fam for p in existing_team if p is not None for fam in p.get('evolution_family', [])}
    max_attempts = 100
    
    if generation and generation in GENERATION_RANGES:
        min_id, max_id = GENERATION_RANGES[generation]
        available_pokemon = [p for p in pokemon_cache.values() 
                           if p and min_id <= p['id'] <= max_id]
    else:
        available_pokemon = [p for p in pokemon_cache.values() if p]

    if not available_pokemon:
        return None
    
    current_legendaries = len([p for p in existing_team if p and p.get('is_legendary')])
    remaining_legendaries = max(0, legendary_count - current_legendaries)
    
    legendaries = [p for p in available_pokemon if p and p.get('is_legendary')]
    regulars = [p for p in available_pokemon if p and not p.get('is_legendary')]
    
    attempts = 0
    while attempts < max_attempts:
        attempts += 1
        
        if remaining_legendaries > 0 and legendaries:
            pokemon = random.choice(legendaries)
            if pokemon and not any(fam in used_families for fam in pokemon.get('evolution_family', [])):
                return pokemon
        
        if regulars:
            pokemon = random.choice(regulars)
            if pokemon and not any(fam in used_families for fam in pokemon.get('evolution_family', [])):
                return pokemon
    
    return None

@app.route('/reroll_pokemon', methods=['POST'])
def reroll_pokemon():
    if 'team' not in session:
        return jsonify({'error': 'No team to reroll from'}), 400
    
    try:
        legendary_count = int(request.form.get('legendary_count', 0))
        generation = request.form.get('generation')
        index = int(request.form.get('index', 0))
        team = session['team']
    except (TypeError, ValueError):
        return jsonify({'error': 'Invalid input parameters'}), 400

    pokemon = get_random_pokemon(
        [p for i, p in enumerate(team) if p is not None and i != index],
        legendary_count,
        generation
    )
    
    if pokemon:
        team[index] = pokemon
        session['team'] = team
        session.modified = True
        return jsonify({'pokemon': pokemon}), 200
    else:
        return jsonify({'error': 'Failed to find a valid Pokémon'}), 400

@app.route('/generate_team', methods=['POST'])
def generate_team():
    legendary_count = int(request.form.get('legendary_count', 0))
    generation = request.form.get('generation')
    team_size = int(request.form.get('team_size', 6))
    
    session['form_values'] = {
        'legendary_count': legendary_count,
        'generation': generation,
        'team_size': team_size
    }
    
    # Generate Pokémon team
    new_team = []
    for _ in range(team_size):
        pokemon = get_random_pokemon(
            [p for p in new_team if p is not None],
            legendary_count,
            generation
        )
        new_team.append(pokemon if pokemon else None)
    
    session['team'] = new_team
    session.modified = True
    return jsonify({'team': session['team']}), 200

@app.route('/', methods=['GET'])
def index():
    # Default form values
    form_values = {
        'legendary_count': 0,
        'generation': None,
        'team_size': 6
    }
    
    # Update with session values if they exist
    if 'form_values' in session:
        form_values.update({
            'legendary_count': session['form_values'].get('legendary_count', 0),
            'generation': session['form_values'].get('generation'),
            'team_size': session['form_values'].get('team_size', 6)
        })
    
    team = session.get('team', [])
    return render_template('index.html',
                         team=team,
                         legendary_count=form_values['legendary_count'],
                         generation=form_values['generation'],
                         team_size=form_values['team_size'])

if __name__ == '__main__':
    if not pokemon_cache:
        pre_cache_pokemon()
    
    scheduler = BackgroundScheduler()
    scheduler.add_job(update_cache, 'cron', hour=3)
    scheduler.start()
    
    app.run(debug=True, port=8000)