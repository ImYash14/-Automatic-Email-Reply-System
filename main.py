import os
import logging
from dotenv import load_dotenv
from langchain_groq import ChatGroq
from langchain_core.prompts import ChatPromptTemplate
from sqlalchemy import create_engine, Column, Integer, String, Float, Boolean
from sqlalchemy.orm import declarative_base, sessionmaker
from contextlib import contextmanager
import re
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

# Load environment variables
load_dotenv()

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Get API key from environment variable
GROQ_API_KEY = os.environ["GROQ_API_KEY"] = ""
if not GROQ_API_KEY:
    raise ValueError("GROQ_API_KEY environment variable is not set")

# Initialize the ChatGroq model
chat = ChatGroq(temperature=0, model_name="mixtral-8x7b-32768")

Base = declarative_base()

# Database setup
engine = create_engine('sqlite:///film_equipment_rental.db')
Session = sessionmaker(bind=engine)

@contextmanager
def session_scope():
    """Provide a transactional scope around a series of operations."""
    session = Session()
    try:
        yield session
        session.commit()
    except Exception as e:
        session.rollback()
        logger.error(f"Database error: {e}")
        raise
    finally:
        session.close()

class Equipment(Base):
    __tablename__ = 'equipment'
    
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    category = Column(String, nullable=False)
    price = Column(Float, nullable=False)
    available = Column(Boolean, default=True)

# Create the database schema if it doesn't exist
Base.metadata.create_all(engine)

# Sample questions for each category
positive_review_samples = [
    "I loved the camera I rented! It worked perfectly.",
    "The service was excellent and the equipment was top-notch.",
    "I had a great experience renting from your company.",
]

negative_review_samples = [
    "The camera I rented was faulty and ruined my shoot.",
    "I'm very disappointed with the service I received.",
    "The equipment was not as described and caused me problems.",
]

general_inquiry_samples = [
    "Do you have any RED cameras available for next week?",
    "What are your rental rates for lighting equipment?",
    "Can I extend my current rental for two more days?",
]

def classify_email(email_content):
    try:
        classification_prompt = ChatPromptTemplate.from_messages([
            ("system", "You are an AI assistant that classifies emails into three categories: 'positive_review', 'negative_review', or 'general_inquiry'. Respond with ONLY the category name, nothing else."),
            ("human", f"""
    Classify the following email into one of these categories: 'positive_review', 'negative_review', or 'general_inquiry':

    {email_content}

    Classification:""")
        ])
        
        chain = classification_prompt | chat
        response = chain.invoke({"email_content": email_content})
        raw_classification = response.content.strip().lower()
        logger.info(f"Raw classification response: {raw_classification}")
        
        # Extract the category using regex
        match = re.search(r'(positive_review|negative_review|general_inquiry)', raw_classification)
        if match:
            classified_category = match.group(1)
            logger.info(f"Extracted classification: {classified_category}")
            return classified_category
        else:
            logger.warning(f"Unexpected classification: {raw_classification}")
            return "error"
    except Exception as e:
        logger.error(f"Error classifying email: {str(e)}")
        return "error"

def extract_equipment_name(email_content):
    try:
        extraction_prompt = ChatPromptTemplate.from_messages([
            ("system", "You are an AI assistant that extracts equipment names from emails."),
            ("human", "Extract the name of the film equipment mentioned in the following email:\n\n{email_content}\n\nEquipment name:")
        ])
        
        chain = extraction_prompt | chat
        response = chain.invoke({"email_content": email_content})
        return response.content.strip()
    except Exception as e:
        logger.error(f"Error extracting equipment name: {e}")
        return None


def handle_inquiry(email_content):
    rag_response = rag_pipeline(email_content)
    if rag_response:
        return rag_response
    
    equipment_name = extract_equipment_name(email_content)
    if equipment_name:
        with session_scope() as session:
            equipment = session.query(Equipment).filter_by(name=equipment_name).first()
            
            if equipment and equipment.available:
                return f"The {equipment.name} is available for rent at ${equipment.price} per day."
            else:
                if equipment:
                    similar_items = session.query(Equipment).filter_by(category=equipment.category, available=True).limit(3).all()
                else:
                    similar_items = session.query(Equipment).filter_by(available=True).limit(3).all()
                
                if similar_items:
                    suggestions = ", ".join([f"{item.name} (${item.price}/day)" for item in similar_items])
                    return f"Unfortunately, the {equipment_name} is not available. You might consider: {suggestions}"
                else:
                    return "We're sorry, but we don't have any similar items available at the moment."
    
    return "Thank you for your inquiry. As we couldn't find specific information related to your question, please contact our customer service for further assistance."

def rag_pipeline(query):
    matched_question, matched_answer = find_best_match(query)
    if matched_answer:
        return matched_answer
    else:
        return None
    
def classify_email(email_content):
    classification_prompt = ChatPromptTemplate.from_messages([
        ("system", "You are an AI assistant that classifies emails into three categories: 'positive_review', 'negative_review', or 'general_inquiry'. Respond with ONLY the category name, nothing else."),
        ("human", f"""
Classify the following email into one of these categories: 'positive_review', 'negative_review', or 'general_inquiry':

{email_content}

Classification:""")
    ])
    
    chain = classification_prompt | chat
    response = chain.invoke({"email_content": email_content})
    return response.content.strip().lower()

def handle_email(email_content):
    try:
        category = classify_email(email_content)
        
        if category == "positive_review":
            prompt = ChatPromptTemplate.from_template(
                "You are responding to a positive review about a film equipment rental service. "
                "Thank the customer and encourage them to share their experience on social media. "
                "Review: {review}"
            )
            chain = prompt | chat
            response = chain.invoke({"review": email_content})
            return category, response.content
        elif category == "negative_review":
            prompt = ChatPromptTemplate.from_template(
                "You are responding to a negative review about a film equipment rental service. "
                "Apologize for the inconvenience, offer a solution, and mention that a customer service "
                "representative will call them. Also, offer a gift voucher for their next rental. "
                "Review: {review}"
            )
            chain = prompt | chat
            response = chain.invoke({"review": email_content})
            return category, response.content
        elif category == "general_inquiry":
            return "general_inquiry", handle_general_inquiry(email_content)
        else:
            return "forward_to_customer_service", "This email requires further evaluation and has been forwarded to our customer service team. They will contact you shortly."
    except Exception as e:
        logger.error(f"Error handling email: {e}")
        return "error", "We encountered an error processing your email. A customer service representative will contact you shortly."

    
    
faq_document = """
FAQ for Film Equipment Rentals

Q: How do I properly set up the RED DSMC2 camera?
A: To set up the RED DSMC2 camera:
   1. Attach the battery
   2. Insert the memory card
   3. Attach desired lens
   4. Power on the camera
   5. Configure settings via the touch screen

Q: What's the battery life of the Canon EF 24-70mm lens?
A: The Canon EF 24-70mm is a passive lens and doesn't require a battery. Its functionality depends on the camera body it's attached to.

Q: How do I balance the DJI Ronin-S for my camera?
A: To balance the DJI Ronin-S:
   1. Balance the vertical tilt
   2. Balance the roll axis
   3. Balance the horizontal tilt
   4. Balance the pan axis
   Refer to the user manual for detailed steps for each axis.

Q: How do I adjust the color temperature on the ARRI SkyPanel S60-C?
A: To adjust color temperature on the ARRI SkyPanel S60-C:
   1. Press the COLOR button
   2. Use the encoder knob to adjust the CCT (Correlated Color Temperature)
   3. Values typically range from 2800K to 10000K

Q: What's the maximum output of the ARRI SkyPanel S60-C?
A: The ARRI SkyPanel S60-C has a maximum output of 1268 lux at 3 meters (9.8 feet) when set to 5600K (daylight).
"""

faq_lines = faq_document.split('\n')
questions = [line[3:] for line in faq_lines if line.startswith('Q: ')]
answers = [line[3:] for line in faq_lines if line.startswith('A: ')]

# Create TF-IDF vectorizer
vectorizer = TfidfVectorizer()
question_vectors = vectorizer.fit_transform(questions)

def find_best_match(query, threshold=0.3):
    query_vector = vectorizer.transform([query])
    similarities = cosine_similarity(query_vector, question_vectors)[0]
    best_match_index = similarities.argmax()
    if similarities[best_match_index] >= threshold:
        return questions[best_match_index], answers[best_match_index]
    return None, None


def handle_general_inquiry(email_content):
    prompt = ChatPromptTemplate.from_messages([
        ("system", """You are an AI assistant for a film equipment rental service. 
        You have access to a FAQ document. Your task is to answer user queries based on this document. 
        If the query can be answered using the FAQ, provide the answer. 
        If the query cannot be answered using the FAQ, politely inform the user to contact customer service.
        
        FAQ Document:
        {faq_document}
        """),
        ("human", "{query}")
    ])
    
    chain = prompt | chat
    response = chain.invoke({"faq_document": faq_document, "query": email_content})
    return response.content.strip()





def add_sample_data():
    sample_equipment = [
        Equipment(name="RED DSMC2", category="Cameras", price=850.00, available=True),
        Equipment(name="Canon EF 24-70mm", category="Lenses", price=50.00, available=True),
        Equipment(name="DJI Ronin-S", category="Stabilizers", price=75.00, available=False),
        Equipment(name="ARRI SkyPanel S60-C", category="Lighting", price=200.00, available=True),
    ]
    
    with session_scope() as session:
        for equipment in sample_equipment:
            existing = session.query(Equipment).filter_by(name=equipment.name).first()
            if not existing:
                session.add(equipment)

def main():
    # Add sample data to the database
    add_sample_data()

    # Example usage
    email_samples = [
        "The RED DSMC2 camera I rented was amazing! Crystal clear footage and so easy to use.",
        "I'm extremely disappointed with the DJI Ronin-S. It was old and didn't work properly.",
        "How do I properly set up the RED DSMC2 camera?",
        "How do I adjust the color temperature on the ARRI SkyPanel S60-C",
        "What's the maximum output of the ARRI SkyPanel S60-C?",
        "I'm looking for information about underwater housing for cameras.",
        "How to return the purchase product",
    ]

    for email in email_samples:
        category, response = handle_email(email)
        logger.info(f"Email: {email}")
        logger.info(f"Category: {category}")
        logger.info(f"Response: {response}\n")

if __name__ == "__main__":
    main()
    
