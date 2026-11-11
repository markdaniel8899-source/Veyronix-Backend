"""
test Profile whether format generate complies OASIS Require
validate：
1. Twitter Profile generate CSV format
2. Reddit Profile generate JSON detailed format
"""

import os
import sys
import json
import csv
import tempfile

# add project path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.oasis_profile_generator import OasisProfileGenerator, OasisAgentProfile


def test_profile_formats():
    """test Profile format"""
    print("=" * 60)
    print("OASIS Profile format test")
    print("=" * 60)
    
    # create test Profile data
    test_profiles = [
        OasisAgentProfile(
            user_id=0,
            user_name="test_user_123",
            name="Test User",
            bio="A test user for validation",
            persona="Test User is an enthusiastic participant in social discussions.",
            karma=1500,
            friend_count=100,
            follower_count=200,
            statuses_count=500,
            age=25,
            gender="male",
            mbti="INTJ",
            country="China",
            profession="Student",
            interested_topics=["Technology", "Education"],
            source_entity_uuid="test-uuid-123",
            source_entity_type="Student",
        ),
        OasisAgentProfile(
            user_id=1,
            user_name="org_official_456",
            name="Official Organization",
            bio="Official account for Organization",
            persona="This is an official institutional account that communicates official positions.",
            karma=5000,
            friend_count=50,
            follower_count=10000,
            statuses_count=200,
            profession="Organization",
            interested_topics=["Public Policy", "Announcements"],
            source_entity_uuid="test-uuid-456",
            source_entity_type="University",
        ),
    ]
    
    generator = OasisProfileGenerator.__new__(OasisProfileGenerator)
    
    # use temporary directory
    with tempfile.TemporaryDirectory() as temp_dir:
        twitter_path = os.path.join(temp_dir, "twitter_profiles.csv")
        reddit_path = os.path.join(temp_dir, "reddit_profiles.json")
        
        # test Twitter CSV format
        print("\n1. test Twitter Profile (CSV format)")
        print("-" * 40)
        generator._save_twitter_csv(test_profiles, twitter_path)
        
        # read and validate CSV
        with open(twitter_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            
        print(f"   file: {twitter_path}")
        print(f"   line count: {len(rows)}")
        print(f"   Table Head: {list(rows[0].keys())}")
        print(f"\n   example data (No.1 line):")
        for key, value in rows[0].items():
            print(f"     {key}: {value}")
        
        # validate required field
        required_twitter_fields = ['user_id', 'user_name', 'name', 'bio', 
                                   'friend_count', 'follower_count', 'statuses_count', 'created_at']
        missing = set(required_twitter_fields) - set(rows[0].keys())
        if missing:
            print(f"\n   [error] Missing field: {missing}")
        else:
            print(f"\n   [pass] all required fields are present in")
        
        # test Reddit JSON format
        print("\n2. test Reddit Profile (JSON detailed format)")
        print("-" * 40)
        generator._save_reddit_json(test_profiles, reddit_path)
        
        # read and validate JSON
        with open(reddit_path, 'r', encoding='utf-8') as f:
            reddit_data = json.load(f)
        
        print(f"   file: {reddit_path}")
        print(f"   Eye count: {len(reddit_data)}")
        print(f"   field: {list(reddit_data[0].keys())}")
        print(f"\n   example data (No.1 NO QUERY SPECIFIED. EXAMPLE REQUEST: GET?Q=HELLO&LANGPAIR=EN|IT):")
        print(json.dumps(reddit_data[0], ensure_ascii=False, indent=4))
        
        # validate detailed format field
        required_reddit_fields = ['realname', 'username', 'bio', 'persona']
        optional_reddit_fields = ['age', 'gender', 'mbti', 'country', 'profession', 'interested_topics']
        
        missing = set(required_reddit_fields) - set(reddit_data[0].keys())
        if missing:
            print(f"\n   [error] Missing required field: {missing}")
        else:
            print(f"\n   [pass] all required fields are present in")
        
        present_optional = set(optional_reddit_fields) & set(reddit_data[0].keys())
        print(f"   [Information] optional field: {present_optional}")
    
    print("\n" + "=" * 60)
    print("test completed!")
    print("=" * 60)


def show_expected_formats():
    """show OASIS Expectations of format"""
    print("\n" + "=" * 60)
    print("OASIS Expectations of Profile format reference")
    print("=" * 60)
    
    print("\n1. Twitter Profile (CSV format)")
    print("-" * 40)
    twitter_example = """user_id,user_name,name,bio,friend_count,follower_count,statuses_count,created_at
0,user0,User Zero,I am user zero with interests in technology.,100,150,500,2023-01-01
1,user1,User One,Tech enthusiast and coffee lover.,200,250,1000,2023-01-02"""
    print(twitter_example)
    
    print("\n2. Reddit Profile (JSON detailed format)")
    print("-" * 40)
    reddit_example = [
        {
            "realname": "James Miller",
            "username": "millerhospitality",
            "bio": "Passionate about hospitality & tourism.",
            "persona": "James is a seasoned professional in the Hospitality & Tourism industry...",
            "age": 40,
            "gender": "male",
            "mbti": "ESTJ",
            "country": "UK",
            "profession": "Hospitality & Tourism",
            "interested_topics": ["Economics", "Business"]
        }
    ]
    print(json.dumps(reddit_example, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    test_profile_formats()
    show_expected_formats()


