import os
from flask import Flask, render_template, request, redirect, url_for, session
import rdflib
from rdflib import Namespace
import threading

app = Flask(__name__)
app.secret_key = "job_matching_session_key"

# Namespaces
EX = Namespace("http://example.org/jobmatch#")

# Load graph
ONTOLOGY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "job_matching_ontology.ttl")
g = rdflib.Graph()
g.parse(ONTOLOGY_PATH, format="turtle")

# Thread lock for SPARQL query parsing (pyparsing is not thread-safe in concurrent environments)
sparql_lock = threading.Lock()

def safe_query(query_str):
    with sparql_lock:
        return list(g.query(query_str))

# -------------------------------------------------------------
# DATABASE QUERIES (SPARQL)
# -------------------------------------------------------------

def get_stats():
    prefix = """
    PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    PREFIX ex: <http://example.org/jobmatch#>
    """
    q_companies = prefix + "SELECT (COUNT(?c) as ?count) WHERE { ?c rdf:type ex:Company }"
    q_jobs = prefix + "SELECT (COUNT(?j) as ?count) WHERE { ?j rdf:type ex:Job }"
    q_skills = prefix + "SELECT (COUNT(?s) as ?count) WHERE { ?s rdf:type ?cat . ?cat rdfs:subClassOf ex:Skill }"
    q_applicants = prefix + "SELECT (COUNT(?a) as ?count) WHERE { ?a rdf:type ex:Applicant }"
    
    comp_count = int(safe_query(q_companies)[0][0])
    job_count = int(safe_query(q_jobs)[0][0])
    skill_count = int(safe_query(q_skills)[0][0])
    app_count = int(safe_query(q_applicants)[0][0])
    
    return {
        "companies": comp_count,
        "jobs": job_count,
        "skills": skill_count,
        "applicants": app_count,
        "triples": len(g)
    }

def get_companies():
    query = """
    PREFIX ex: <http://example.org/jobmatch#>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    SELECT ?company ?label ?location ?industry
    WHERE {
        ?company rdf:type ex:Company ;
                 rdfs:label ?label ;
                 ex:hasLocation ?location ;
                 ex:hasIndustry ?industry .
    }
    ORDER BY ?label
    """
    results = safe_query(query)
    companies = []
    for row in results:
        companies.append({
            "id": str(row.company).split("#")[-1],
            "name": str(row.label),
            "location": str(row.location),
            "industry": str(row.industry)
        })
    return companies

def get_skills():
    query = """
    PREFIX ex: <http://example.org/jobmatch#>
    PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    SELECT ?skill ?label ?catLabel
    WHERE {
        ?skill rdfs:label ?label ;
               rdf:type ?cat .
        ?cat rdfs:subClassOf ex:Skill ;
             rdfs:label ?catLabel .
    }
    ORDER BY ?catLabel ?label
    """
    results = safe_query(query)
    skills = []
    for row in results:
        skills.append({
            "id": str(row.skill).split("#")[-1],
            "label": str(row.label),
            "category": str(row.catLabel)
        })
    return skills

def get_jobs():
    query = """
    PREFIX ex: <http://example.org/jobmatch#>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    SELECT ?job ?label ?compLabel ?level ?reqEdu ?location ?industry
    WHERE {
        ?job rdf:type ex:Job ;
             rdfs:label ?label ;
             ex:offeredBy ?company ;
             ex:hasJobLevel ?level ;
             ex:hasRequiredDegreeLevel ?reqEdu .
        ?company rdfs:label ?compLabel ;
                 ex:hasLocation ?location ;
                 ex:hasIndustry ?industry .
    }
    ORDER BY ?label
    """
    results = safe_query(query)
    jobs = []
    for row in results:
        job_uri = row.job
        
        # Get required skills
        skills_query = """
        PREFIX ex: <http://example.org/jobmatch#>
        PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
        SELECT ?skill ?label
        WHERE {
            <%s> ex:requiresSkill ?skill .
            ?skill rdfs:label ?label .
        }
        """ % job_uri
        
        job_skills = []
        for s_row in safe_query(skills_query):
            job_skills.append({
                "id": str(s_row.skill).split("#")[-1],
                "label": str(s_row.label)
            })
            
        jobs.append({
            "id": str(job_uri).split("#")[-1],
            "title": str(row.label),
            "company": str(row.compLabel),
            "location": str(row.location),
            "industry": str(row.industry) if hasattr(row, 'industry') else "",
            "level": str(row.level),
            "reqEdu": str(row.reqEdu),
            "skills": job_skills
        })
    return jobs

def get_predefined_profiles():
    query = """
    PREFIX ex: <http://example.org/jobmatch#>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    SELECT ?applicant ?name ?type ?degreeName ?degreeLevel ?fieldOfStudy ?institution ?gradYear ?expRole ?expOrg ?expDuration ?expType
    WHERE {
        ?applicant rdf:type ex:Applicant ;
                   ex:hasName ?name ;
                   ex:hasApplicantType ?type ;
                   ex:hasEducation ?edu .
        ?edu ex:hasDegreeName ?degreeName ;
             ex:hasDegreeLevel ?degreeLevel ;
             ex:hasFieldOfStudy ?fieldOfStudy ;
             ex:hasInstitution ?institution ;
             ex:hasGraduationYear ?gradYear .
        OPTIONAL {
            ?applicant ex:hasExperience ?exp .
            ?exp ex:hasRole ?expRole ;
                 ex:hasOrganisation ?expOrg ;
                 ex:hasDurationMonths ?expDuration ;
                 ex:hasExperienceType ?expType .
        }
    }
    ORDER BY ?name
    """
    results = safe_query(query)
    profiles = []
    for row in results:
        app_uri = row.applicant
        
        # Get applicant skills
        skills_query = """
        PREFIX ex: <http://example.org/jobmatch#>
        PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
        SELECT ?skill ?label
        WHERE {
            <%s> ex:hasSkill ?skill .
            ?skill rdfs:label ?label .
        }
        """ % app_uri
        
        app_skills = []
        for s_row in safe_query(skills_query):
            app_skills.append(str(s_row.skill).split("#")[-1])
            
        profiles.append({
            "id": str(app_uri).split("#")[-1],
            "name": str(row.name),
            "type": str(row.type),
            "degreeName": str(row.degreeName),
            "degreeLevel": str(row.degreeLevel),
            "fieldOfStudy": str(row.fieldOfStudy) if hasattr(row, 'fieldOfStudy') else "",
            "institution": str(row.institution),
            "gradYear": int(row.gradYear),
            "skills": app_skills,
            "experience": {
                "role": str(row.expRole),
                "org": str(row.expOrg),
                "duration": int(row.expDuration) if row.expDuration else 0,
                "type": str(row.expType)
            } if row.expRole else None
        })
    return profiles

# -------------------------------------------------------------
# MATCHING LOGIC
# -------------------------------------------------------------

def calculate_matches(user_profile):
    jobs = get_jobs()
    match_results = []
    
    user_skills_set = set(user_profile.get("skills", []))
    user_degree_level = user_profile.get("degreeLevel", "Bachelor")
    
    all_skills = get_skills()
    skills_map = {s["id"]: s for s in all_skills}
    
    for job in jobs:
        job_skills_set = {s["id"] for s in job["skills"]}
        
        # Intersection & difference
        matched_ids = user_skills_set.intersection(job_skills_set)
        missing_ids = job_skills_set.difference(user_skills_set)
        
        matched_skills = [skills_map[sid] for sid in matched_ids if sid in skills_map]
        missing_skills = [skills_map[sid] for sid in missing_ids if sid in skills_map]
        
        # Match percentage
        total_req_skills = len(job["skills"])
        base_score = int((len(matched_skills) / total_req_skills) * 100) if total_req_skills > 0 else 0
        
        # Experience bonus
        score = base_score
        exp_bonus_applied = False
        if user_profile.get("experience") and user_profile["experience"].get("duration", 0) > 0:
            score = min(base_score + 10, 100)
            if score > base_score:
                exp_bonus_applied = True
        
        # Education match check (Bachelor covers both, Diploma covers only Diploma)
        job_req_edu = job["reqEdu"]
        if job_req_edu == "Bachelor":
            edu_match = (user_degree_level == "Bachelor")
        else:
            edu_match = True
            
        match_results.append({
            "job": job,
            "score": score,
            "matched_skills": matched_skills,
            "missing_skills": missing_skills,
            "edu_match": edu_match,
            "exp_bonus_applied": exp_bonus_applied,
            "status": "Excellent Match" if (score >= 75 and edu_match) else
                      "Good Match" if (score >= 50 and edu_match) else
                      "Education Mismatch" if (not edu_match) else "Skill Gap"
        })
        
    match_results.sort(key=lambda x: (x["edu_match"], x["score"]), reverse=True)
    return match_results

# -------------------------------------------------------------
# CONTROLLERS (ROUTES)
# -------------------------------------------------------------

@app.route("/", methods=["GET", "POST"])
def home():
    skills = get_skills()
    
    if request.method == "POST":
        selected_skills = request.form.getlist("skills")
        exp_type = request.form.get("exp_type", "None")
        exp_data = None
        if exp_type != "None":
            exp_data = {
                "type": exp_type,
                "role": request.form.get("exp_role", ""),
                "org": request.form.get("exp_org", ""),
                "duration": int(request.form.get("exp_duration")) if request.form.get("exp_duration") else 0
            }
        session["user_profile"] = {
            "name": request.form.get("name"),
            "type": request.form.get("type"),
            "degreeName": request.form.get("degreeName"),
            "degreeLevel": request.form.get("degreeLevel"),
            "fieldOfStudy": request.form.get("fieldOfStudy", ""),
            "institution": request.form.get("institution"),
            "gradYear": int(request.form.get("gradYear")),
            "skills": selected_skills,
            "experience": exp_data
        }
        session.modified = True
        return redirect(url_for("home"))
        
    user_profile = session.get("user_profile")
    
    # Render setup form if profile is empty
    if not user_profile:
        grouped_skills = {}
        for s in skills:
            cat = s["category"]
            if cat not in grouped_skills:
                grouped_skills[cat] = []
            grouped_skills[cat].append(s)
        return render_template("setup_profile.html", grouped_skills=grouped_skills, active_page="home")
        
    # Render matching dashboard
    stats = get_stats()
    all_skills = get_skills()
    skills_map = {s["id"]: s for s in all_skills}
    user_skills_objects = [skills_map[sid] for sid in user_profile["skills"] if sid in skills_map]
    
    matches = calculate_matches(user_profile)
    return render_template(
        "index.html",
        stats=stats,
        user_profile=user_profile,
        user_skills=user_skills_objects,
        matches=matches,
        active_page="home"
    )

@app.route("/edit_profile", methods=["GET", "POST"])
def edit_profile():
    skills = get_skills()
    user_profile = session.get("user_profile")
    
    if not user_profile:
        return redirect(url_for("home"))
        
    grouped_skills = {}
    for s in skills:
        cat = s["category"]
        if cat not in grouped_skills:
            grouped_skills[cat] = []
        grouped_skills[cat].append(s)
        
    if request.method == "POST":
        selected_skills = request.form.getlist("skills")
        exp_type = request.form.get("exp_type", "None")
        exp_data = None
        if exp_type != "None":
            exp_data = {
                "type": exp_type,
                "role": request.form.get("exp_role", ""),
                "org": request.form.get("exp_org", ""),
                "duration": int(request.form.get("exp_duration")) if request.form.get("exp_duration") else 0
            }
        session["user_profile"] = {
            "name": request.form.get("name"),
            "type": request.form.get("type"),
            "degreeName": request.form.get("degreeName"),
            "degreeLevel": request.form.get("degreeLevel"),
            "fieldOfStudy": request.form.get("fieldOfStudy", ""),
            "institution": request.form.get("institution"),
            "gradYear": int(request.form.get("gradYear")),
            "skills": selected_skills,
            "experience": exp_data
        }
        session.modified = True
        return redirect(url_for("home"))
        
    return render_template("edit_profile.html", grouped_skills=grouped_skills, user_profile=user_profile, active_page="edit")

@app.route("/candidates")
def candidates():
    profiles = get_predefined_profiles()
    all_skills = get_skills()
    skills_map = {s["id"]: s for s in all_skills}
    
    candidates_data = []
    for p in profiles:
        p_skills = [skills_map[sid] for sid in p["skills"] if sid in skills_map]
        matches = calculate_matches(p)
        best_match = matches[0] if matches else None
        
        candidates_data.append({
            "profile": p,
            "skills": p_skills,
            "best_match": best_match
        })
        
    return render_template("candidates.html", candidates=candidates_data, active_page="candidates")

@app.route("/load_profile/<profile_id>")
def load_profile(profile_id):
    predefined = get_predefined_profiles()
    for p in predefined:
        if p["id"] == profile_id:
            session["user_profile"] = {
                "name": p["name"],
                "type": p["type"],
                "degreeName": p["degreeName"],
                "degreeLevel": p["degreeLevel"],
                "fieldOfStudy": p.get("fieldOfStudy", ""),
                "institution": p["institution"],
                "gradYear": p["gradYear"],
                "skills": p["skills"],
                "experience": p.get("experience")
            }
            session.modified = True
            break
    return redirect(url_for("home"))

@app.route("/model", methods=["GET", "POST"])
def semantic_model():
    query_str = request.form.get("query")
    results = None
    error = None
    variables = []
    
    # Default query for the user to see
    if not query_str:
        query_str = """PREFIX ex: <http://example.org/jobmatch#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>

SELECT ?jobLabel ?companyLabel ?level
WHERE {
    ?job rdf:type ex:Job ;
         rdfs:label ?jobLabel ;
         ex:offeredBy ?company ;
         ex:hasJobLevel ?level .
    ?company rdfs:label ?companyLabel .
}
LIMIT 5"""
        
    if request.method == "POST":
        try:
            raw_res = safe_query(query_str)
            variables = [str(var) for var in raw_res.vars] if hasattr(raw_res, 'vars') else []
            results = []
            for row in raw_res:
                results.append([str(val) for val in row])
        except Exception as e:
            error = str(e)
            
    return render_template(
        "semantic_model.html", 
        active_page="model", 
        query=query_str, 
        results=results, 
        error=error,
        variables=variables
    )

@app.route("/reset")
def reset_profile():
    session.clear()
    return redirect(url_for("home"))

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)

