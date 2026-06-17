import os
from flask import Flask, render_template, request, redirect, url_for, session
import rdflib
from rdflib import Namespace
import threading
import time

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

def get_skills_with_tags():
    flat_skills = get_skills()
    skills_dict = {}
    for s in flat_skills:
        sid = s["id"]
        if sid not in skills_dict:
            skills_dict[sid] = {
                "id": sid,
                "label": s["label"],
                "categories": []
            }
        if s["category"] not in skills_dict[sid]["categories"]:
            skills_dict[sid]["categories"].append(s["category"])
    return list(skills_dict.values())


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
        
        # Get required skills (including core skills)
        skills_query = """
        PREFIX ex: <http://example.org/jobmatch#>
        PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
        SELECT ?skill ?label ?isCore
        WHERE {
            {
                <%s> ex:requiresSkill ?skill .
                BIND(false AS ?isCore)
            } UNION {
                <%s> ex:requiresCoreSkill ?skill .
                BIND(true AS ?isCore)
            }
            ?skill rdfs:label ?label .
        }
        """ % (job_uri, job_uri)
        
        job_skills = []
        for s_row in safe_query(skills_query):
            job_skills.append({
                "id": str(s_row.skill).split("#")[-1],
                "label": str(s_row.label),
                "isCore": bool(s_row.isCore)
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
            
        p_name = str(row.name)
        p_loc = "Kuala Lumpur"
        if "Web Design" in p_name:
            p_loc = "Petaling Jaya"
        elif "Cybersecurity" in p_name:
            p_loc = "Cyberjaya"
        elif "Data Analysis" in p_name:
            p_loc = "Penang"

        profiles.append({
            "id": str(app_uri).split("#")[-1],
            "name": p_name,
            "type": str(row.type),
            "location": p_loc,
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
    
    # Semantic inference mapping (if user has subclass/specialized skill, infer the base skill)
    user_skills_set = set(user_profile.get("skills", []))
    INFERRED_SKILLS = {
        "React": {"JavaScript", "HTML", "CSS"},
        "NodeJS": {"JavaScript"},
        "DeepLearning": {"MachineLearning", "Python"},
        "MachineLearning": {"Python", "DataAnalysis"},
        "NLP": {"Python"},
        "GitHub": {"Git"},
        "Kubernetes": {"Docker"},
        "PenetrationTesting": {"Cybersecurity"},
    }
    inferred_skills = set()
    for skill in user_skills_set:
        if skill in INFERRED_SKILLS:
            inferred_skills.update(INFERRED_SKILLS[skill])
            
    expanded_user_skills = user_skills_set.union(inferred_skills)
    user_degree_level = user_profile.get("degreeLevel", "Bachelor")
    user_location = user_profile.get("location", "Kuala Lumpur")
    
    all_skills = get_skills()
    skills_map = {s["id"]: s for s in all_skills}
    
    # Job-specific Core Skills vs Secondary Skills
    JOB_CORE_SKILLS = {
        "AIEngineer": {"MachineLearning", "DeepLearning", "NLP"},
        "DataScientist": {"MachineLearning", "DataAnalysis", "Python"},
        "FrontendDeveloper": {"JavaScript", "React"},
        "BackendDeveloper": {"Java", "NodeJS", "APIIntegration"},
        "FullStackDeveloper": {"React", "NodeJS", "JavaScript"},
        "CybersecurityAnalyst": {"Cybersecurity", "PenetrationTesting"},
        "CloudArchitect": {"CloudComputingAWS", "Docker", "Kubernetes"},
        "DevOpsEngineer": {"Docker", "Kubernetes", "CloudComputingAWS"},
        "UXUIDesigner": {"HTML", "CSS"},
        "DataEngineer": {"SQL", "DataAnalysis", "Python"},
        "SystemAdministrator": {"Cybersecurity", "CloudComputingAWS"},
        "MobileAppDeveloper": {"JavaScript", "React"},
        "QAAutomationEngineer": {"Python", "JavaScript"},
        "SoftwareTester": {"HTML", "CSS"}
    }
    
    SKILL_COURSES = {
        "Python": "Python for Everybody Specialization (Coursera)",
        "MachineLearning": "Machine Learning Specialization by Andrew Ng (Coursera)",
        "DeepLearning": "Deep Learning Specialization by DeepLearning.AI",
        "NLP": "Natural Language Processing Specialization (Coursera)",
        "DataAnalysis": "Google Data Analytics Professional Certificate",
        "React": "React - The Complete Guide (Udemy)",
        "NodeJS": "The Complete Node.js Developer Course (Udemy)",
        "JavaScript": "Modern JavaScript From The Beginning",
        "SQL": "SQL for Data Science (Coursera)",
        "Cybersecurity": "Google Cybersecurity Professional Certificate",
        "PenetrationTesting": "CompTIA PenTest+ Pathway",
        "CloudComputingAWS": "AWS Certified Solutions Architect Course",
        "Docker": "Docker Mastery on Udemy",
        "Kubernetes": "Certified Kubernetes Administrator (CKA)",
        "Git": "Git Complete: The Definitive Guide (Udemy)",
        "GitHub": "GitHub Ultimate: Master Git and GitHub",
        "APIIntegration": "REST APIs with Flask and Python",
        "HTML": "HTML & CSS for Beginners",
        "CSS": "CSS - The Complete Guide (Udemy)"
    }
    
    KLANG_VALLEY_CITIES = {"Kuala Lumpur", "Petaling Jaya", "Cyberjaya"}
    
    for job in jobs:
        job_id = job["id"]
        job_skills_set = {s["id"] for s in job["skills"]}
        
        ontology_core_set = {s["id"] for s in job["skills"] if s.get("isCore")}
        if ontology_core_set:
            core_set = ontology_core_set
        else:
            core_set = JOB_CORE_SKILLS.get(job_id, set())
            
        if not core_set:
            core_set = set(list(job_skills_set)[:3])
            
        secondary_set = job_skills_set.difference(core_set)
        
        matched_core = expanded_user_skills.intersection(core_set)
        matched_secondary = expanded_user_skills.intersection(secondary_set)
        
        missing_core = core_set.difference(expanded_user_skills)
        missing_secondary = secondary_set.difference(expanded_user_skills)
        
        # Calculate base score based on weights: Core = 70%, Secondary = 30%
        core_score = (len(matched_core) / len(core_set) * 70) if core_set else 70
        secondary_score = (len(matched_secondary) / len(secondary_set) * 30) if secondary_set else 30
        base_score = int(core_score + secondary_score)
        
        # Core skills sanity check (Cannot be a Good Match without at least one core skill matched)
        has_at_least_one_core = (len(matched_core) > 0) or (not core_set)
        
        # Experience level compatibility matching
        cand_exp_months = user_profile.get("experience", {}).get("duration", 0) if user_profile.get("experience") else 0
        
        exp_match = True
        exp_bonus = 0
        exp_feedback = "Meets requirement"
        if job["level"] == "Junior" and cand_exp_months < 12:
            exp_match = False
            exp_bonus = -10  # Penalty for junior roles requiring 1yr experience
            exp_feedback = "Experience Gap (Requires 12+ months)"
        elif cand_exp_months > 0:
            exp_bonus = 10   # Bonus for having experience
            exp_feedback = "Experience Match Bonus Applied (+10%)"
            
        # Location matching (Proximity / Commute matching)
        job_loc_clean = job["location"].split(",")[0].strip()
        cand_loc_clean = user_location.split(",")[0].strip()
        
        loc_score_modifier = 0
        loc_feedback = "Perfect Match"
        if cand_loc_clean == job_loc_clean:
            loc_score_modifier = 0
        elif cand_loc_clean in KLANG_VALLEY_CITIES and job_loc_clean in KLANG_VALLEY_CITIES:
            loc_score_modifier = -5
            loc_feedback = "Nearby Commute (Klang Valley)"
        else:
            loc_score_modifier = -20
            loc_feedback = "Relocation Required"
            
        # Final Score
        score = max(0, min(100, base_score + exp_bonus + loc_score_modifier))
        
        # Education match check
        job_req_edu = job["reqEdu"]
        edu_match = True
        if job_req_edu == "Bachelor" and user_degree_level != "Bachelor":
            edu_match = False
            
        # Actionable recommendations
        recommendations = []
        for skill_id in missing_core:
            if skill_id in skills_map:
                recommendations.append({
                    "skill_label": skills_map[skill_id]["label"],
                    "course": SKILL_COURSES.get(skill_id, "Online Tutorials & Certification Courses"),
                    "type": "Core"
                })
        for skill_id in missing_secondary:
            if skill_id in skills_map:
                recommendations.append({
                    "skill_label": skills_map[skill_id]["label"],
                    "course": SKILL_COURSES.get(skill_id, "Online Tutorials & Certification Courses"),
                    "type": "Secondary"
                })
                
        # Match status
        if not has_at_least_one_core:
            status = "Core Skill Gap"
        elif not edu_match:
            status = "Education Mismatch"
        elif score >= 75:
            status = "Excellent Match"
        elif score >= 50:
            status = "Good Match"
        else:
            status = "Skill Gap"

        matched_skills_list = []
        for sid in expanded_user_skills.intersection(job_skills_set):
            if sid in skills_map:
                s_dict = dict(skills_map[sid])
                s_dict["is_core"] = (sid in core_set)
                matched_skills_list.append(s_dict)
                
        missing_skills_list = []
        for sid in job_skills_set.difference(expanded_user_skills):
            if sid in skills_map:
                s_dict = dict(skills_map[sid])
                s_dict["is_core"] = (sid in core_set)
                missing_skills_list.append(s_dict)
            
        match_results.append({
            "job": job,
            "score": score,
            "core_skills_count": len(core_set),
            "matched_core_count": len(matched_core),
            "secondary_skills_count": len(secondary_set),
            "matched_secondary_count": len(matched_secondary),
            "matched_skills": matched_skills_list,
            "missing_skills": missing_skills_list,
            "edu_match": edu_match,
            "exp_match": exp_match,
            "exp_feedback": exp_feedback,
            "loc_feedback": loc_feedback,
            "loc_match_score": loc_score_modifier,
            "recommendations": recommendations,
            "status": status
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
            "location": request.form.get("location", "Kuala Lumpur"),
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
        return render_template("setup_profile.html", skills_with_tags=get_skills_with_tags(), active_page="home")
        
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
            "location": request.form.get("location", "Kuala Lumpur"),
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
        
    return render_template("edit_profile.html", user_profile=user_profile, active_page="edit", skills_with_tags=get_skills_with_tags())

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
                "location": p.get("location", "Kuala Lumpur"),
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

@app.route("/post_job", methods=["GET", "POST"])
def post_job():
    if request.method == "POST":
        company_option = request.form.get("company_option")
        
        # 1. Determine Company URI
        if company_option == "existing":
            comp_id = request.form.get("existing_company")
            company_uri = EX[comp_id]
        else:
            comp_name = request.form.get("company_name", "New Company").strip()
            comp_loc = request.form.get("company_location", "Kuala Lumpur").strip()
            comp_ind = request.form.get("company_industry", "Technology").strip()
            
            # Generate ID slug
            comp_slug = "".join(x for x in comp_name if x.isalnum())
            if not comp_slug:
                comp_slug = "Company_" + str(int(time.time()))
            company_uri = EX[comp_slug]
            
            # Add company to RDF Graph
            g.add((company_uri, rdflib.RDF.type, EX.Company))
            g.add((company_uri, rdflib.RDFS.label, rdflib.Literal(comp_name)))
            g.add((company_uri, EX.hasLocation, rdflib.Literal(comp_loc)))
            g.add((company_uri, EX.hasIndustry, rdflib.Literal(comp_ind)))
            
        # 2. Determine Job URI and details
        job_title = request.form.get("job_title", "Software Engineer").strip()
        job_level = request.form.get("job_level", "Entry-Level")
        req_edu = request.form.get("req_edu", "Bachelor")
        
        job_slug = "".join(x for x in job_title if x.isalnum()) + "_" + str(int(time.time()))
        job_uri = EX[job_slug]
        
        # Add Job to RDF Graph
        g.add((job_uri, rdflib.RDF.type, EX.Job))
        g.add((job_uri, rdflib.RDFS.label, rdflib.Literal(job_title)))
        g.add((job_uri, EX.offeredBy, company_uri))
        g.add((job_uri, EX.hasJobLevel, rdflib.Literal(job_level)))
        g.add((job_uri, EX.hasRequiredDegreeLevel, rdflib.Literal(req_edu)))
        
        # 3. Add Skills
        # Form inputs: core_skills and secondary_skills (comma separated string of IDs)
        core_skills_str = request.form.get("core_skills", "")
        secondary_skills_str = request.form.get("secondary_skills", "")
        
        core_skills = [s.strip() for s in core_skills_str.split(",") if s.strip()]
        secondary_skills = [s.strip() for s in secondary_skills_str.split(",") if s.strip()]
        
        for s_id in core_skills:
            g.add((job_uri, EX.requiresCoreSkill, EX[s_id]))
                
        for s_id in secondary_skills:
            g.add((job_uri, EX.requiresSkill, EX[s_id]))
                
        # 4. Serialize / Save changes back to .ttl file
        g.serialize(ONTOLOGY_PATH, format="turtle")
        
        return redirect(url_for("candidates")) # Redirect to the graduates/jobs list page to see it!
        
    companies = get_companies()
    return render_template(
        "post_job.html", 
        companies=companies, 
        skills_with_tags=get_skills_with_tags(), 
        active_page="post_job"
    )

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)

